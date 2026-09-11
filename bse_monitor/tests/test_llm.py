"""LLM extraction layer. Entirely offline — no API key, no SDK required."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from bse_monitor.classifier.ml import blend
from bse_monitor.database import repository as repo
from bse_monitor.database.models import CATEGORIES, LlmExtraction
from bse_monitor.llm.client import (
    ANTHROPIC_AVAILABLE,
    LlmClient,
    LlmUsage,
    estimate_cost,
)
from bse_monitor.llm.extractor import (
    LlmEventExtractor,
    announcement_key,
    page_text_for,
)
from bse_monitor.llm.prompts import INSTRUCTIONS, SYSTEM_PROMPT, build_user_content, system_blocks
from bse_monitor.llm.reconcile import (
    merge_verdict_into_extraction,
    reconcile,
    reconcile_three_way,
    verdict_anchor,
)
from bse_monitor.llm.schema import (
    EVENT_CLASSES,
    EVENT_JSON_SCHEMA,
    HOLDER_TYPES,
    LlmVerdict,
    coerce_event,
)
from bse_monitor.parser.entities import ExtractionResult
from bse_monitor.tests.conftest import FakeClient, make_response

GOOD = {
    "event_class": "OFS", "holder": "Blackstone Capital Partners",
    "holder_type": "PE_VC", "stake_pct": 6.2,
    "effective_date": "2026-09-15", "confidence": 0.91,
}


# -- optional dependency ---------------------------------------------------
def test_client_unavailable_without_sdk_or_injection() -> None:
    """Absent the SDK the layer reports unavailable rather than exploding."""
    assert LlmClient().available is ANTHROPIC_AVAILABLE


def test_llm_is_disabled_in_the_shipped_config(config) -> None:
    assert config.get("llm.enabled") is False


def test_extract_event_returns_error_when_unavailable() -> None:
    call = LlmClient(client=None).extract_event(title="t", page_text="x")
    assert call.ok is False and call.error


# -- the JSON contract -----------------------------------------------------
def test_schema_is_strict() -> None:
    assert EVENT_JSON_SCHEMA["additionalProperties"] is False
    assert len(EVENT_JSON_SCHEMA["required"]) == 6


def test_event_class_enum_matches_the_db_check_constraint() -> None:
    """A mismatch here fails the INSERT and takes the whole filing down."""
    assert tuple(EVENT_CLASSES) == tuple(CATEGORIES)
    assert EVENT_JSON_SCHEMA["properties"]["event_class"]["enum"] == list(CATEGORIES)


def test_unknown_event_class_is_clamped_to_other() -> None:
    verdict = coerce_event({**GOOD, "event_class": "MegaDump"})
    assert verdict.event_class == "Other"
    assert verdict.confidence == 0.0 and verdict.valid is False


def test_unknown_holder_type_is_clamped() -> None:
    assert coerce_event({**GOOD, "holder_type": "ALIEN"}).holder_type == "UNKNOWN"


@pytest.mark.parametrize("stake", [0, -5, 250, "banana"])
def test_out_of_range_stake_is_dropped(stake) -> None:
    assert coerce_event({**GOOD, "stake_pct": stake}).stake_pct is None


def test_iso_date_parsed_and_null_tolerated() -> None:
    assert coerce_event(GOOD).effective_date == dt.date(2026, 9, 15)
    assert coerce_event({**GOOD, "effective_date": None}).effective_date is None


def test_unparseable_date_invalidates_rather_than_raises() -> None:
    verdict = coerce_event({**GOOD, "effective_date": "sometime next year"})
    assert verdict.effective_date is None and verdict.valid is False


def test_missing_keys_survive() -> None:
    verdict = coerce_event({})
    assert verdict.event_class == "Other" and verdict.confidence == 0.0


def test_good_payload_round_trips() -> None:
    verdict = coerce_event(GOOD)
    assert verdict.valid is True
    assert verdict.to_dict()["effective_date"] == "2026-09-15"


# -- prompt and cache ------------------------------------------------------
def test_system_blocks_are_byte_identical_across_calls() -> None:
    """Any per-call variation silently zeroes the cache hit rate forever."""
    assert json.dumps(system_blocks()) == json.dumps(system_blocks())


def test_system_blocks_carry_cache_control() -> None:
    assert all("cache_control" in block for block in system_blocks())
    assert all("cache_control" not in block for block in system_blocks(cache=False))


def test_system_prefix_contains_no_volatile_values() -> None:
    prefix = SYSTEM_PROMPT + INSTRUCTIONS
    for token in (str(dt.date.today()), "filing_id", "content_hash"):
        assert token not in prefix


def test_filing_text_never_enters_the_system_prefix() -> None:
    prefix = SYSTEM_PROMPT + INSTRUCTIONS
    body = build_user_content("Some headline", "SECRET FILING BODY", "pages:1-3")
    assert "SECRET FILING BODY" in body and "SECRET FILING BODY" not in prefix


def test_prompt_documents_its_cache_position() -> None:
    """Haiku 4.5 will not cache below 4096 tokens; the prefix is knowingly under.

    If someone later grows the prefix past the floor, this test is where the
    economics get revisited rather than silently assumed.
    """
    approx_tokens = len(SYSTEM_PROMPT + INSTRUCTIONS) / 3.6
    assert 2000 < approx_tokens < 4096


def test_build_params_puts_volatile_content_last() -> None:
    client = LlmClient(client=FakeClient())
    params = client.build_params(title="T", page_text="BODY", page_slice="pages:1-3")
    assert params["model"] == "claude-haiku-4-5"
    assert "BODY" in params["messages"][0]["content"]
    assert all("BODY" not in block["text"] for block in params["system"])


# -- calls, retry, cost ----------------------------------------------------
def test_successful_call_parses_and_prices() -> None:
    fake = FakeClient([make_response(GOOD, usage={"input_tokens": 3000, "output_tokens": 100})])
    call = LlmClient(client=fake).extract_event(title="t", page_text="body")
    assert call.ok and call.verdict.event_class == "OFS"
    assert call.cost_usd == pytest.approx((3000 * 1.0 + 100 * 5.0) / 1e6)
    assert call.attempts == 1


def test_unparseable_response_is_reported_not_raised() -> None:
    fake = FakeClient([make_response({}, text="this is not json")])
    call = LlmClient(client=fake).extract_event(title="t", page_text="body")
    assert call.ok is False and "not parseable" in call.error


def test_connection_error_never_raises() -> None:
    fake = FakeClient(error=RuntimeError("socket died"))
    call = LlmClient(client=fake, max_retries=1).extract_event(title="t", page_text="b")
    assert call.ok is False and "socket died" in call.error


def test_non_retryable_error_is_not_retried() -> None:
    """Without the SDK's typed exceptions nothing is retryable — fail fast."""
    fake = FakeClient(error=RuntimeError("400 bad request"))
    call = LlmClient(client=fake, max_retries=3, backoff_base=0).extract_event(
        title="t", page_text="b"
    )
    assert call.attempts == 1


@pytest.mark.parametrize(
    "usage,expected",
    [
        (LlmUsage(input_tokens=1_000_000), 1.0),
        (LlmUsage(output_tokens=1_000_000), 5.0),
        (LlmUsage(cache_creation_input_tokens=1_000_000), 1.25),
        (LlmUsage(cache_read_input_tokens=1_000_000), 0.1),
    ],
)
def test_cost_components(usage, expected) -> None:
    assert estimate_cost(usage) == pytest.approx(expected)


def test_batch_costs_half() -> None:
    usage = LlmUsage(input_tokens=1_000_000, output_tokens=200_000)
    assert estimate_cost(usage, batch=True) == pytest.approx(estimate_cost(usage) / 2)


# -- page slicing ----------------------------------------------------------
def test_first_three_pages_excludes_page_four() -> None:
    meta = {"pages": ["one", "two", "three", "four"]}
    text, slice_label = page_text_for(meta, "", max_pages=3)
    assert "four" not in text and slice_label == "pages:1-3"


def test_char_budget_fallback_when_pages_unavailable() -> None:
    """The cached-document path has no page structure; label it honestly."""
    text, slice_label = page_text_for({"extracted_text": "x" * 50_000}, "", max_chars=100)
    assert len(text) == 100 and slice_label == "char_budget"


def test_headline_only_when_nothing_else() -> None:
    assert page_text_for({}, "just a headline")[1] == "headline_only"


# -- dedupe and persistence ------------------------------------------------
def _extractor(fake, **kwargs):
    return LlmEventExtractor(LlmClient(client=fake), **kwargs)


def test_same_announcement_is_never_sent_twice(session, calendar_filing) -> None:
    _company, filing = calendar_filing
    fake = FakeClient([make_response(GOOD), make_response(GOOD)])
    extractor = _extractor(fake)

    first = extractor.extract(session, filing, title="t", page_text="b", page_slice="pages:1-3")
    second = extractor.extract(session, filing, title="t", page_text="b", page_slice="pages:1-3")

    assert len(fake.messages.calls) == 1          # the guarantee
    assert first is not None and second is not None
    assert second.event_class == first.event_class
    assert extractor.stats.cached == 1


def test_announcement_key_falls_back_to_content_hash() -> None:
    assert announcement_key("BSE", "123", "abc") == "BSE:123"
    assert announcement_key("BSE", None, "abc") == "hash:abc"


def test_usage_and_verdict_persisted(session, calendar_filing) -> None:
    _company, filing = calendar_filing
    fake = FakeClient([make_response(GOOD, usage={"input_tokens": 2000, "output_tokens": 80})])
    _extractor(fake).extract(session, filing, title="t", page_text="b", page_slice="pages:1-3")

    row = session.query(LlmExtraction).one()
    assert row.status == "OK" and row.event_class == "OFS"
    assert row.input_tokens == 2000 and float(row.cost_usd) > 0
    assert row.holder_normalized == "blackstone capital partners"
    assert row.page_slice == "pages:1-3"


def test_failed_call_is_recorded_as_failed(session, calendar_filing) -> None:
    _company, filing = calendar_filing
    fake = FakeClient(error=RuntimeError("boom"))
    verdict = _extractor(fake).extract(
        session, filing, title="t", page_text="b", page_slice="pages:1-3"
    )
    row = session.query(LlmExtraction).one()
    assert verdict is None and row.status == "FAILED" and "boom" in row.error


def test_raw_response_suppressed_when_disabled(session, calendar_filing) -> None:
    _company, filing = calendar_filing
    fake = FakeClient([make_response(GOOD)])
    _extractor(fake, persist_raw=False).extract(
        session, filing, title="t", page_text="b", page_slice="pages:1-3"
    )
    assert session.query(LlmExtraction).one().raw_response in ({}, None)


def test_claim_is_idempotent(session, calendar_filing) -> None:
    _company, filing = calendar_filing
    first, created_first = repo.claim_llm_extraction(
        session, filing_id=filing.id, announcement_id="BSE:X",
        source="BSE", model="m", prompt_version="v1",
    )
    second, created_second = repo.claim_llm_extraction(
        session, filing_id=filing.id, announcement_id="BSE:X",
        source="BSE", model="m", prompt_version="v1",
    )
    assert created_first is True and created_second is False
    assert first.id == second.id


def test_usage_summary(session, calendar_filing) -> None:
    _company, filing = calendar_filing
    fake = FakeClient([make_response(GOOD, usage={"input_tokens": 1000, "output_tokens": 50})])
    _extractor(fake).extract(session, filing, title="t", page_text="b", page_slice="p")
    summary = repo.llm_usage_summary(session)
    assert summary["calls"] == 1 and summary["ok"] == 1 and summary["cost_usd"] > 0


# -- gating ----------------------------------------------------------------
def test_gating_skips_low_priority_and_confident_other(session, calendar_filing) -> None:
    _company, filing = calendar_filing
    extractor = _extractor(FakeClient())

    filing.category, filing.priority = "OFS", "HIGH"
    assert extractor.should_extract(filing) is True

    filing.category, filing.priority = "OFS", "LOW"
    assert extractor.should_extract(filing) is False

    # An unconfident `Other` is exactly the long tail the LLM is for.
    filing.category, filing.classification_confidence = "Other", 0.1
    assert extractor.should_extract(filing) is True

    filing.classification_confidence = 0.9
    assert extractor.should_extract(filing) is False


def test_max_calls_per_run_is_respected(session, calendar_filing) -> None:
    _company, filing = calendar_filing
    extractor = _extractor(FakeClient(), max_calls_per_run=0)
    filing.category, filing.priority = "OFS", "HIGH"
    assert extractor.should_extract(filing) is False


# -- reconciliation --------------------------------------------------------
def test_confident_rules_beat_the_llm(llm_verdict) -> None:
    assert reconcile("QIP", 0.8, llm_verdict) == ("QIP", 0.8, "rules")


def test_llm_rescues_an_unconfident_other(llm_verdict) -> None:
    category, _confidence, method = reconcile("Other", 0.1, llm_verdict)
    assert (category, method) == ("OFS", "llm")


def test_invalid_verdict_is_ignored() -> None:
    bad = coerce_event({"event_class": "Nonsense"})
    assert reconcile("Other", 0.1, bad) == ("Other", 0.1, "rules")


def test_existing_ml_label_is_unchanged() -> None:
    """Regression guard: parameterising blend() must not move the ML callers."""
    assert blend("Other", 0.1, ("OFS", 0.9))[2] == "ml"
    assert blend("QIP", 0.8, ("QIP", 0.9))[2] == "rules+ml"


def test_three_way_records_its_ordering(llm_verdict) -> None:
    _cat, _conf, _method, evidence = reconcile_three_way("Other", 0.1, ("QIP", 0.4), llm_verdict)
    assert evidence["rule"]["category"] == "Other"
    assert evidence["ml"]["prediction"] == ["QIP", 0.4]
    assert evidence["llm"]["event_class"] == "OFS"
    assert evidence["final"]["category"]


def test_llm_never_overwrites_a_regex_extraction(llm_verdict) -> None:
    """Exact beats probabilistic — the same discipline as _merge_table_signals."""
    extraction = ExtractionResult(percent_of_equity=8.4)
    merged = merge_verdict_into_extraction(extraction, llm_verdict)
    assert merged.percent_of_equity == 8.4


def test_llm_fills_a_gap_the_regex_missed(llm_verdict) -> None:
    merged = merge_verdict_into_extraction(ExtractionResult(), llm_verdict)
    assert merged.percent_of_equity == 6.2


def test_llm_effective_date_becomes_a_calendar_anchor() -> None:
    """Where Layer 2 feeds Layer 1."""
    verdict = LlmVerdict(event_class="IPO", effective_date=dt.date(2026, 3, 10), confidence=0.8)
    anchors = verdict_anchor(verdict, "IPO")
    assert anchors.allotment_date == dt.date(2026, 3, 10)
    assert anchors.confidence == 0.8


def test_verdict_anchor_empty_without_a_date() -> None:
    assert verdict_anchor(LlmVerdict(event_class="IPO"), "IPO").any_known() is False


# -- batch -----------------------------------------------------------------
def _batch_items():
    from bse_monitor.llm.batch import BatchItem, custom_id_for

    return [
        BatchItem(filing_id=i, announcement_id=f"BSE:{i}", custom_id=custom_id_for(i),
                  title=f"title {i}", page_text=f"body {i}", page_slice="pages:1-3")
        for i in (1, 2, 3)
    ]


def test_batch_requests_use_json_schema_not_parse() -> None:
    """Batches have no `parse` equivalent — the raw schema is mandatory."""
    from bse_monitor.llm.batch import build_batch_requests

    requests = build_batch_requests(LlmClient(client=FakeClient()), _batch_items())
    assert all(
        r["params"]["output_config"]["format"]["type"] == "json_schema" for r in requests
    )


def test_batch_shares_one_byte_identical_system_prefix() -> None:
    from bse_monitor.llm.batch import build_batch_requests

    requests = build_batch_requests(LlmClient(client=FakeClient()), _batch_items())
    prefixes = {json.dumps(r["params"]["system"]) for r in requests}
    assert len(prefixes) == 1


def test_batch_custom_ids_are_stable_and_unique() -> None:
    from bse_monitor.llm.batch import custom_id_for

    assert custom_id_for(42) == custom_id_for(42) != custom_id_for(43)


def _batch_result(custom_id, kind="succeeded", payload=None, error_type=""):
    message = make_response(payload or GOOD)

    class Error:
        type = error_type

    class Outcome:
        type = kind

    outcome = Outcome()
    outcome.message = message
    outcome.error = Error()

    class Result:
        pass

    result = Result()
    result.custom_id = custom_id
    result.result = outcome
    return result


def test_batch_results_are_keyed_by_custom_id_not_order(session, calendar_filing) -> None:
    """Results arrive in arbitrary order; position-based matching corrupts rows."""
    from bse_monitor.llm.batch import BatchItem, collect_batch, submit_batch

    _company, filing = calendar_filing
    fake = FakeClient()
    client = LlmClient(client=fake)
    items = [
        BatchItem(filing.id, "BSE:A", "fA", "t", "b", "pages:1-3"),
        BatchItem(filing.id, "BSE:B", "fB", "t", "b", "pages:1-3"),
    ]
    batch_id = submit_batch(client, session, items)

    # Deliberately reversed relative to submission.
    fake.messages.batches.results_payload = [
        _batch_result("fB", payload={**GOOD, "event_class": "QIP"}),
        _batch_result("fA", payload={**GOOD, "event_class": "OFS"}),
    ]
    stats = collect_batch(client, session, batch_id)

    assert stats.succeeded == 2
    by_announcement = {
        r.announcement_id: r.event_class for r in session.query(LlmExtraction).all()
    }
    assert by_announcement == {"BSE:A": "OFS", "BSE:B": "QIP"}


def test_batch_invalid_request_is_terminal(session, calendar_filing) -> None:
    from bse_monitor.llm.batch import BatchItem, collect_batch, submit_batch

    _company, filing = calendar_filing
    fake = FakeClient()
    client = LlmClient(client=fake)
    batch_id = submit_batch(
        client, session, [BatchItem(filing.id, "BSE:A", "fA", "t", "b")]
    )
    fake.messages.batches.results_payload = [
        _batch_result("fA", kind="errored", error_type="invalid_request")
    ]
    stats = collect_batch(client, session, batch_id)
    assert stats.failed == 1
    assert session.query(LlmExtraction).one().status == "FAILED"


def test_batch_expiry_resets_for_retry(session, calendar_filing) -> None:
    from bse_monitor.llm.batch import BatchItem, collect_batch, submit_batch

    _company, filing = calendar_filing
    fake = FakeClient()
    client = LlmClient(client=fake)
    batch_id = submit_batch(
        client, session, [BatchItem(filing.id, "BSE:A", "fA", "t", "b")]
    )
    fake.messages.batches.results_payload = [_batch_result("fA", kind="expired")]
    stats = collect_batch(client, session, batch_id)
    assert stats.retryable == 1
    row = session.query(LlmExtraction).one()
    assert row.status == "PENDING" and row.batch_id is None


def test_batch_cost_is_halved(session, calendar_filing) -> None:
    from bse_monitor.llm.batch import BatchItem, collect_batch, submit_batch

    _company, filing = calendar_filing
    fake = FakeClient()
    client = LlmClient(client=fake)
    batch_id = submit_batch(client, session, [BatchItem(filing.id, "BSE:A", "fA", "t", "b")])
    fake.messages.batches.results_payload = [_batch_result("fA")]
    collect_batch(client, session, batch_id)
    row = session.query(LlmExtraction).one()
    sync_cost = estimate_cost(LlmUsage(input_tokens=3000, output_tokens=120))
    assert float(row.cost_usd) == pytest.approx(sync_cost / 2)

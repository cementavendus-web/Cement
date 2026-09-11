"""Shared fixtures. Tests run entirely offline against an in-memory SQLite DB."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

from bse_monitor.config import Config, load_investors, load_keywords
from bse_monitor.database.models import Base
from bse_monitor.database.session import build_engine
from bse_monitor.scraper.models import RawFiling


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch):
    """Guarantee no test can reach a paid API.

    This matters more than it looks: ``Config.load()`` applies environment
    overrides, so a developer with ``BSE_LLM_ENABLED=true`` exported in their
    shell would silently turn the whole offline suite into a live, billed run.
    The base URL is pointed at a closed port so a leaked call fails instantly
    rather than being charged.
    """
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith(("BSE_LLM", "BSE_TRIGGERS")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")


@pytest.fixture(scope="session")
def keywords():
    return load_keywords()


@pytest.fixture(scope="session")
def investors():
    return load_investors()


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    cfg = Config.load()
    # Point every writable path at the test's tmp dir and disable outbound I/O.
    cfg.data["database"]["url"] = "sqlite://"
    cfg.data["pdf"]["enabled"] = False
    cfg.data["alerts"]["channels"]["csv"]["output_dir"] = str(tmp_path / "exports")
    cfg.data["app"]["log_file"] = str(tmp_path / "test.log")
    cfg.data["scraper"]["use_playwright_fallback"] = False
    # Layer 2 is off in tests; the calendar is pure computation and stays on so
    # the pipeline path through it is actually covered.
    cfg.data["llm"]["enabled"] = False
    cfg.data["llm"]["api_key"] = ""
    cfg.data["llm"]["batch"]["enabled"] = False
    cfg.data["triggers"]["enabled"] = True
    return cfg


@pytest.fixture()
def session():
    from sqlalchemy.orm import sessionmaker

    engine = build_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def sample_raw() -> RawFiling:
    return RawFiling(
        source="BSE",
        source_filing_id="T-1",
        company_name="Vantage Infratech Limited",
        bse_code="500325",
        headline="Board approves fund raising of up to Rs. 1,200 crore by way of Qualified Institutions Placement",
        body_text=(
            "The Board of Directors at its meeting held on 09 September, 2026 has approved "
            "the raising of funds aggregating up to Rs. 1,200 crore by way of a Qualified "
            "Institutions Placement to Qualified Institutional Buyers."
        ),
        filing_datetime=dt.datetime(2026, 9, 9, 16, 12),
    )


@pytest.fixture()
def calendar_filing(session):
    """A company plus an IPO filing carrying known allotment and listing dates."""
    from bse_monitor.database import repository as repo

    company = repo.upsert_company(session, "Zenith Aerospace Systems Limited", bse_code="543999")
    text = (
        "The Company has completed the Initial Public Offering. The date of allotment "
        "is 10 March, 2026 and the equity shares were listed on 13 March, 2026. "
        "Objects of the issue include capital expenditure for setting up of a new plant."
    )
    filing, _ = repo.upsert_filing(
        session,
        {
            "content_hash": repo.content_hash("Zenith Aerospace", "IPO allotment", dt.date(2026, 3, 13)),
            "company_id": company.id,
            "source": "BSE",
            "source_filing_id": "CAL-1",
            "headline": "Allotment of equity shares pursuant to the Initial Public Offering",
            "search_text": text,
            "category": "IPO",
            "classification_confidence": 0.9,
            "opportunity_score": 80,
            "priority": "MEDIUM",
            "filing_date": dt.date(2026, 3, 13),
            "filing_datetime": dt.datetime(2026, 3, 13, 18, 30),
        },
    )
    session.commit()
    return company, filing


class FakeMessages:
    """Recording stand-in for ``client.messages``.

    Injected via ``LlmClient(client=fake)``, so the tests exercise the real
    request-construction and parsing code with no HTTP and no SDK installed.
    """

    def __init__(self, responses=None, error=None):
        self._responses = list(responses or [])
        self._error = error
        self.calls = []
        self.batches = FakeBatches()

    # Only `create` exists, so LlmClient falls through from `parse` to the
    # output_config path — the same branch an older SDK would take.
    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            error = self._error
            if isinstance(error, list):
                error = error.pop(0) if error else None
                if error is None:
                    self._error = None
            if error is not None:
                raise error
        if self._responses:
            return self._responses.pop(0)
        return make_response({"event_class": "Other", "holder": None,
                              "holder_type": "UNKNOWN", "stake_pct": None,
                              "effective_date": None, "confidence": 0.5})


class FakeBatches:
    def __init__(self):
        self.submitted = []
        self.results_payload = []

    def create(self, requests):
        self.submitted.append(requests)
        return type("Batch", (), {"id": "msgbatch_test", "processing_status": "in_progress"})()

    def retrieve(self, batch_id):
        return type("Batch", (), {"id": batch_id, "processing_status": "ended"})()

    def results(self, batch_id):
        return iter(self.results_payload)


class FakeClient:
    def __init__(self, responses=None, error=None):
        self.messages = FakeMessages(responses, error)


def make_response(payload, *, usage=None, text=None):
    """A minimal object shaped like a Messages API response."""
    import json as _json

    class Usage:
        input_tokens = (usage or {}).get("input_tokens", 3000)
        output_tokens = (usage or {}).get("output_tokens", 120)
        cache_creation_input_tokens = (usage or {}).get("cache_creation_input_tokens", 0)
        cache_read_input_tokens = (usage or {}).get("cache_read_input_tokens", 0)

    class Block:
        type = "text"

    block = Block()
    block.text = text if text is not None else _json.dumps(payload)

    class Response:
        content = [block]

    response = Response()
    response.usage = Usage()
    return response


@pytest.fixture()
def fake_anthropic():
    return FakeClient


@pytest.fixture()
def llm_verdict():
    from bse_monitor.llm.schema import LlmVerdict

    return LlmVerdict(
        event_class="OFS", holder="Blackstone", holder_type="PE_VC",
        stake_pct=6.2, effective_date=dt.date(2026, 9, 15), confidence=0.91,
    )

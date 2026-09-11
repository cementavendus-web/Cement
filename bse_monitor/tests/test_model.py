"""Baseline, LightGBM, walk-forward evaluation and SHAP.

The synthetic panel below has *known* structure: sells are driven by trigger
proximity, marquee status and position size. A model that cannot beat the
baseline on data where the signal is real and stated would not be worth
shipping, so that comparison is asserted rather than hoped for.
"""

from __future__ import annotations

import datetime as dt
import random

import pytest

from bse_monitor.model.baseline import baseline_reasons, baseline_score, baseline_scores
from bse_monitor.model.evaluate import (
    fold_windows,
    format_report,
    median_lead_time,
    pr_auc,
    precision_at_k_per_week,
)
from bse_monitor.model.features import FEATURE_NAMES
from bse_monitor.model.train import (
    LIGHTGBM_AVAILABLE,
    SHAP_AVAILABLE,
    describe,
    load_model,
    save_model,
    shap_reasons,
    train,
    walk_forward,
)


def synthetic_panel(seed: int = 7, years=(2022, 2023, 2024, 2025), weeks_per_year: int = 45,
                    names_per_week: int = 110):
    """A panel with structure a tree can learn and an additive heuristic cannot.

    Deliberately *not* a monotone function of the baseline's own inputs — that
    would make the baseline Bayes-optimal by construction and the comparison
    meaningless. Instead the generating process contains the interactions that
    are the actual reason to reach for a tree model here:

    * marquee holders sell hard into an *anchor* lock-in release but barely at
      all into a promoter lock-in, which is a different constraint entirely;
    * a large stake in an illiquid name is a block-deal candidate, while the
      same stake in a liquid name simply dribbles out and never trips the
      threshold;
    * risk is non-monotone in days-to-trigger — inside a week the sale is
      usually already announced, so the marginal forward risk falls again.

    Base rate is ~5%, in the range a real weekly panel produces.
    """
    rng = random.Random(seed)
    rows = []
    for year in years:
        for week in range(weeks_per_year):
            week_end = dt.date(year, 1, 3) + dt.timedelta(weeks=week)
            for name in range(names_per_week):
                days = rng.choice([None, 3, 12, 25, 45, 80, 150, 300])
                marquee = int(rng.random() < 0.25)
                stake_cr = rng.choice([10, 60, 300, 1200, 5000])
                prior = rng.randint(0, 4)
                adv = rng.choice([0.5, 2.0, 6.0, 15.0])
                is_anchor = int(days is not None and days <= 90 and rng.random() < 0.5)
                is_promoter_trigger = int(days is not None and not is_anchor)

                risk = 0.012
                # Non-monotone in proximity: inside a week it is usually already out.
                if days is not None:
                    risk += {3: 0.05, 12: 0.16, 25: 0.12, 45: 0.06, 80: 0.02}.get(days, 0.005)
                # Interaction 1: marquee x anchor release, not marquee alone.
                if marquee and is_anchor and days is not None and days <= 45:
                    risk += 0.30
                elif marquee and is_promoter_trigger:
                    risk += 0.01
                # Interaction 2: size only matters when the name is illiquid.
                if stake_cr >= 1000 and adv >= 6.0:
                    risk += 0.22
                elif stake_cr >= 1000:
                    risk += 0.02
                if prior >= 3 and marquee:
                    risk += 0.05

                # Scaled to land the base rate near 6%, the range a real
                # weekly panel produces.
                label = int(rng.random() < min(risk * 0.45, 0.9))

                rows.append({
                    "company_id": name, "company": f"Co {name}", "bse_code": str(name),
                    "holder_key": f"holder{name % 23}", "holder": f"Holder {name % 23}",
                    "week_end": week_end, "year": year,
                    "days_to_nearest_trigger": days,
                    "triggers_within_30d": int(days is not None and days <= 30),
                    "triggers_within_60d": int(days is not None and days <= 60),
                    "triggers_within_90d": int(days is not None and days <= 90),
                    "nearest_trigger_is_anchor": is_anchor,
                    "nearest_trigger_is_promoter": is_promoter_trigger,
                    "days_since_ipo": rng.randint(30, 2000),
                    "holder_prior_sells_here": prior,
                    "holder_prior_sells_anywhere": prior * 2,
                    "days_since_holder_last_sell": rng.randint(10, 400),
                    "holder_weeks_observed": rng.randint(5, 200),
                    "holder_sell_rate": round(rng.random() * 0.1, 4),
                    "last_stake_pct": round(rng.random() * 10, 2),
                    "stake_value_cr": stake_cr,
                    "days_of_adv_to_liquidate": adv,
                    "filings_4w": rng.randint(0, 5),
                    "filings_12w": rng.randint(0, 15),
                    "max_score_12w": rng.randint(0, 100),
                    "sell_filings_12w": rng.randint(0, 3),
                    "is_marquee": marquee,
                    "label": label,
                    "lead_time_days": rng.randint(1, 60) if label else None,
                })
    return rows


@pytest.fixture(scope="module")
def panel():
    return synthetic_panel()


# -- baseline --------------------------------------------------------------
def test_baseline_prefers_the_nearer_deadline() -> None:
    near = baseline_score({"days_to_nearest_trigger": 9})
    far = baseline_score({"days_to_nearest_trigger": 150})
    assert near > far


def test_baseline_rewards_marquee_and_size() -> None:
    base = {"days_to_nearest_trigger": 25}
    assert baseline_score({**base, "is_marquee": 1}) > baseline_score(base)
    assert baseline_score({**base, "stake_value_cr": 5000}) > baseline_score(base)


def test_baseline_is_bounded() -> None:
    loaded = {"days_to_nearest_trigger": 1, "is_marquee": 1, "stake_value_cr": 99999,
              "holder_prior_sells_here": 5, "days_of_adv_to_liquidate": 20}
    assert 0 <= baseline_score(loaded) <= 100
    assert baseline_score({}) == 0


def test_baseline_gives_readable_reasons() -> None:
    reasons = baseline_reasons({"days_to_nearest_trigger": 12, "is_marquee": 1,
                                "stake_value_cr": 800})
    assert len(reasons) == 3 and "12d" in reasons[0]


def test_baseline_has_real_skill_on_the_synthetic_panel(panel) -> None:
    """If the baseline had no skill, beating it would prove nothing."""
    scores = baseline_scores(panel)
    labels = [r["label"] for r in panel]
    base_rate = sum(labels) / len(labels)
    assert 0.02 < base_rate < 0.12, f"unrealistic base rate {base_rate:.3f}"
    assert pr_auc(labels, scores) > base_rate * 1.4


# -- metrics ---------------------------------------------------------------
def test_precision_at_k_is_computed_per_week() -> None:
    """Pooling weeks would let one busy week carry the whole score."""
    rows = [
        {"week_end": dt.date(2026, 1, 2), "label": 1},
        {"week_end": dt.date(2026, 1, 2), "label": 0},
        {"week_end": dt.date(2026, 1, 9), "label": 0},
        {"week_end": dt.date(2026, 1, 9), "label": 0},
    ]
    precision, weeks = precision_at_k_per_week(rows, [1.0, 0.1, 1.0, 0.1], k=1)
    assert weeks == 2 and precision == 0.5


def test_median_lead_time_uses_true_positives_only() -> None:
    rows = [
        {"week_end": dt.date(2026, 1, 2), "label": 1, "lead_time_days": 30},
        {"week_end": dt.date(2026, 1, 2), "label": 0, "lead_time_days": None},
        {"week_end": dt.date(2026, 1, 9), "label": 1, "lead_time_days": 10},
    ]
    assert median_lead_time(rows, [1.0, 0.9, 1.0], k=2) == 20.0


def test_pr_auc_ranks_perfectly_and_randomly() -> None:
    assert pr_auc([1, 1, 0, 0], [0.9, 0.8, 0.2, 0.1]) == 1.0
    assert pr_auc([0, 0, 0], [0.9, 0.8, 0.2]) == 0.0


def test_folds_are_walk_forward_with_an_embargo() -> None:
    """Training weeks whose labels resolve in the test year would leak it."""
    folds = fold_windows([2022, 2023, 2024], embargo_days=60)
    assert [f["test_year"] for f in folds] == [2023, 2024]
    assert folds[0]["train_years"] == [2022]
    assert folds[0]["train_cutoff"] == dt.date(2022, 11, 2)      # 60 days before year end
    assert folds[1]["train_years"] == [2022, 2023]


def test_first_year_is_never_a_test_fold() -> None:
    assert fold_windows([2022]) == []


# -- training --------------------------------------------------------------
@pytest.mark.skipif(not LIGHTGBM_AVAILABLE, reason="LightGBM not installed")
def test_model_trains_and_sets_class_weight(panel) -> None:
    model = train(panel[:4000])
    assert model is not None
    assert model.feature_set_version == "v1"
    # Sell events are rare; without this the model predicts "no" everywhere.
    assert model.params["scale_pos_weight"] > 1


def test_single_class_training_set_is_refused() -> None:
    rows = [{**r, "label": 0} for r in synthetic_panel(weeks_per_year=2, names_per_week=5,
                                                       years=(2024, 2025))]
    assert train(rows) is None


@pytest.mark.skipif(not LIGHTGBM_AVAILABLE, reason="LightGBM not installed")
def test_model_beats_the_baseline_where_signal_is_real(panel) -> None:
    folds = walk_forward(panel, k=20)
    assert folds, "walk-forward produced no folds"
    beaten = sum(1 for f in folds if f.pr_auc > f.baseline_pr_auc)
    assert beaten == len(folds), format_report(folds)


@pytest.mark.skipif(not LIGHTGBM_AVAILABLE, reason="LightGBM not installed")
def test_precision_at_20_beats_the_base_rate(panel) -> None:
    for fold in walk_forward(panel, k=20):
        assert fold.precision_at_k > fold.base_rate
        assert fold.lift > 1.0


@pytest.mark.skipif(not LIGHTGBM_AVAILABLE, reason="LightGBM not installed")
def test_lead_time_is_reported(panel) -> None:
    folds = walk_forward(panel, k=20)
    assert all(f.median_lead_time_days is not None for f in folds)


def test_report_shows_every_year_separately(panel) -> None:
    folds = walk_forward(panel, k=20)
    report = format_report(folds)
    for fold in folds:
        assert str(fold.year) in report
    assert "baseline" in report.lower()


# -- persistence -----------------------------------------------------------
@pytest.mark.skipif(not LIGHTGBM_AVAILABLE, reason="LightGBM not installed")
def test_model_round_trips(panel, tmp_path) -> None:
    model = train(panel[:3000])
    path = save_model(model, tmp_path / "m.joblib")
    loaded = load_model(path)
    assert loaded is not None and loaded.feature_set_version == model.feature_set_version


@pytest.mark.skipif(not LIGHTGBM_AVAILABLE, reason="LightGBM not installed")
def test_feature_set_mismatch_refuses_to_load(panel, tmp_path, monkeypatch) -> None:
    """Scoring across feature sets is a wrong answer, not an error — so refuse."""
    import joblib

    model = train(panel[:3000])
    path = save_model(model, tmp_path / "m.joblib")
    payload = joblib.load(path)
    payload["feature_set_version"] = "v0-ancient"
    joblib.dump(payload, path)
    assert load_model(path) is None


def test_missing_model_file_returns_none(tmp_path) -> None:
    assert load_model(tmp_path / "absent.joblib") is None


# -- SHAP ------------------------------------------------------------------
@pytest.mark.skipif(not (LIGHTGBM_AVAILABLE and SHAP_AVAILABLE), reason="needs lightgbm+shap")
def test_shap_gives_three_readable_reasons(panel) -> None:
    model = train(panel[:3000])
    reasons = shap_reasons(model, panel[:5], top_n=3)
    assert len(reasons) == 5
    assert all(1 <= len(r) <= 3 for r in reasons)
    # Readable, not raw feature names.
    assert not any("days_to_nearest_trigger=" in reason for row in reasons for reason in row)


def test_shap_falls_back_to_baseline_reasons_without_a_model(panel) -> None:
    reasons = shap_reasons(None, panel[:3])
    assert len(reasons) == 3


def test_phrasebook_covers_every_feature() -> None:
    """A feature with no phrase renders as a raw column name in the brief."""
    from bse_monitor.model.train import PHRASEBOOK

    assert set(FEATURE_NAMES) <= set(PHRASEBOOK)


def test_describe_handles_a_bad_value() -> None:
    assert describe("stake_value_cr", None)
    assert describe("unknown_feature", 3) == "unknown_feature=3"

"""Configuration loading and environment overrides."""

from __future__ import annotations

import pytest

from bse_monitor.config import Config, apply_env_overrides, load_investors, load_keywords


def test_defaults_load() -> None:
    cfg = Config.load()
    assert cfg.get("app.name") == "bse-monitor"
    assert cfg.get("scoring.base_scores.QIP") == 90
    assert cfg.get("missing.key", "fallback") == "fallback"


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("BSE_CLASSIFIER_MIN_CONFIDENCE", "0.9")
    monkeypatch.setenv("BSE_ALERTS_ENABLED", "false")
    monkeypatch.setenv("BSE_SCRAPER_BSE_CATEGORIES", "Board Meeting,Corp. Action")
    cfg = Config.load()
    assert cfg.get("classifier.min_confidence") == 0.9
    assert cfg.get("alerts.enabled") is False
    assert cfg.get("scraper.bse_categories") == ["Board Meeting", "Corp. Action"]


def test_database_url_convention(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://u:p@h/db")
    assert Config.load().get("database.url") == "postgresql+psycopg2://u:p@h/db"


@pytest.mark.parametrize(
    "raw,expected", [("true", True), ("12", 12), ("1.5", 1.5), ("none", None), ("plain", "plain")]
)
def test_value_coercion(raw, expected, monkeypatch) -> None:
    tree = {"app": {"probe": "default"}}
    monkeypatch.setenv("BSE_APP_PROBE", raw)
    assert apply_env_overrides(tree)["app"]["probe"] == expected


def test_resolve_path_creates_directory(tmp_path) -> None:
    cfg = Config.load()
    cfg.data["pdf"]["download_dir"] = str(tmp_path / "nested" / "pdfs")
    assert cfg.resolve_path("pdf.download_dir", "./data/pdfs").exists()


def test_lexicon_shape() -> None:
    lexicon = load_keywords()
    required = {"IPO", "FPO", "QIP", "RightsIssue", "OFS", "BlockDeal", "InvestorExit",
                "PromoterSale", "FundRaise", "PreferentialAllotment"}
    assert required <= set(lexicon["categories"])
    for name, spec in lexicon["categories"].items():
        assert spec.get("strong"), f"{name} has no decisive terms"
        assert spec.get("saturation", 0) > 0


def test_investor_registry_shape() -> None:
    registry = load_investors()
    names = {entry["name"] for entry in registry["investors"]}
    expected = {"Blackstone", "KKR", "Bain Capital", "TPG", "Warburg Pincus", "Carlyle",
                "General Atlantic", "Temasek", "GIC", "ADIA", "CPPIB", "Apax", "ChrysCapital"}
    assert expected <= names
    assert all(entry.get("aliases") for entry in registry["investors"])


def test_scoring_categories_cover_the_taxonomy() -> None:
    from bse_monitor.database.models import CATEGORIES

    base_scores = Config.load().get("scoring.base_scores")
    assert set(CATEGORIES) <= set(base_scores)

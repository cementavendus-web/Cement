"""Shared fixtures. Tests run entirely offline against an in-memory SQLite DB."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from bse_monitor.config import Config, load_investors, load_keywords
from bse_monitor.database.models import Base
from bse_monitor.database.session import build_engine
from bse_monitor.scraper.models import RawFiling


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

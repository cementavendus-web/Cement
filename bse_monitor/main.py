"""Command-line entry point.

    python -m bse_monitor.main init-db
    python -m bse_monitor.main run --days 2
    python -m bse_monitor.main backfill --from 2026-08-01 --to 2026-08-31
    python -m bse_monitor.main report daily --date 2026-09-11
    python -m bse_monitor.main report fundraise --csv out.csv
    python -m bse_monitor.main report selldown
    python -m bse_monitor.main alerts flush
    python -m bse_monitor.main classify "Board approves QIP of Rs 1200 crore"
    python -m bse_monitor.main train-model --min-samples 200
    python -m bse_monitor.main schedule --print-cron
    python -m bse_monitor.main seed-demo        # loads examples/sample_filings.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from sqlalchemy import select

from .alerts.formatting import markdown_table
from .config import Config
from .database import repository as repo
from .database.models import EventFiling
from .database.session import create_all, init_engine, session_scope
from .logging_setup import setup_logging
from .pipeline import MonitorPipeline
from .reports import (
    CALENDAR_COLUMNS,
    DAILY_COLUMNS,
    calendar_digest,
    company_watchlist,
    daily_events,
    fund_raise_pipeline,
    sell_down_pipeline,
    upcoming_triggers_report,
    write_csv,
)
from .scraper.models import RawFiling

log = logging.getLogger("bse_monitor.main")


def bootstrap(args: argparse.Namespace) -> Config:
    config = Config.load(getattr(args, "config", None))
    setup_logging(
        level=getattr(args, "log_level", None) or config.get("app.log_level", "INFO"),
        log_file=config.get("app.log_file"),
    )
    init_engine(
        config.get("database.url"),
        echo=bool(config.get("database.echo", False)),
        pool_size=int(config.get("database.pool_size", 5)),
        max_overflow=int(config.get("database.max_overflow", 10)),
    )
    return config


def _parse_date(value: Optional[str]) -> Optional[dt.date]:
    if not value:
        return None
    return dt.date.fromisoformat(value)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_init_db(args: argparse.Namespace) -> int:
    bootstrap(args)
    create_all()
    with session_scope() as session:
        print(json.dumps(repo.counts(session), indent=2))
    print("Schema ready.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = bootstrap(args)
    create_all()
    pipeline = MonitorPipeline(config)
    to_date = _parse_date(args.to_date) or dt.date.today()
    from_date = _parse_date(args.from_date)
    if from_date is None and args.days:
        from_date = to_date - dt.timedelta(days=args.days)

    overall = 0
    for source in args.sources:
        with session_scope() as session:
            stats = pipeline.run(
                session,
                from_date=from_date,
                to_date=to_date,
                source=source,
                send_alerts=not args.no_alerts,
                limit=args.limit,
            )
        print(json.dumps(stats.as_dict(), indent=2))
        overall += len(stats.errors)
    return 0 if overall == 0 else 2


def cmd_backfill(args: argparse.Namespace) -> int:
    """Re-cover a historical window in day-sized chunks.

    Chunking keeps each BSE query inside the API's practical result ceiling and
    means a failure loses one day rather than the whole range.
    """
    config = bootstrap(args)
    create_all()
    pipeline = MonitorPipeline(config)
    to_date = _parse_date(args.to_date) or dt.date.today()
    from_date = _parse_date(args.from_date) or (to_date - dt.timedelta(days=args.days))

    totals = {"seen": 0, "new": 0, "duplicates": 0, "errors": 0}
    day = from_date
    while day <= to_date:
        with session_scope() as session:
            try:
                stats = pipeline.run(
                    session,
                    from_date=day,
                    to_date=day,
                    source=args.source,
                    send_alerts=False,
                )
            except Exception as exc:
                log.error("Backfill day failed", extra={"day": str(day), "error": str(exc)})
                totals["errors"] += 1
                day += dt.timedelta(days=1)
                continue
        totals["seen"] += stats.seen
        totals["new"] += stats.new
        totals["duplicates"] += stats.duplicates
        totals["errors"] += len(stats.errors)
        print(f"{day.isoformat()}: seen={stats.seen} new={stats.new} dup={stats.duplicates}")
        day += dt.timedelta(days=1)
    print(json.dumps(totals, indent=2))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    bootstrap(args)
    with session_scope() as session:
        if args.kind == "daily":
            rows = daily_events(session, _parse_date(args.date), min_score=args.min_score)
            columns: Sequence[str] = DAILY_COLUMNS
            title = f"Daily Events — {(_parse_date(args.date) or dt.date.today()).isoformat()}"
        elif args.kind == "fundraise":
            rows = fund_raise_pipeline(session, min_score=args.min_score)
            columns = ("company", "event_type", "stage", "issue_size", "value", "score", "date")
            title = "Fund Raise Pipeline"
        elif args.kind == "calendar":
            rows = upcoming_triggers_report(
                session, within_days=args.within_days, min_confidence=args.min_confidence
            )
            columns = CALENDAR_COLUMNS
            title = f"Trigger Calendar — next {args.within_days} days"
        elif args.kind == "selldown":
            rows = sell_down_pipeline(session, min_score=args.min_score)
            columns = ("company", "event_type", "seller", "stake_pct", "trigger", "value", "score")
            title = "Potential Sell-Down Pipeline"
        else:
            rows = company_watchlist(session, min_score=args.min_score)
            columns = ("company", "bse_code", "events", "categories", "max_score", "latest")
            title = "Company Watchlist"

        print(f"\n### {title}\n")
        print(markdown_table(rows, list(columns)))
        print()
        if args.csv:
            path = write_csv(rows, args.csv, columns)
            print(f"Wrote {len(rows)} rows to {path}")
    return 0


def cmd_calendar(args: argparse.Namespace) -> int:
    """Refresh, list or expire the forward deadline calendar."""
    config = bootstrap(args)
    create_all()
    from .triggers.refresh import expire_triggers, refresh_calendar

    offsets = dict(config.get("triggers.offsets", {}) or {})
    rule_version = args.rule_version or config.get("triggers.rule_version", "v1")

    with session_scope() as session:
        if args.action == "refresh":
            stats = refresh_calendar(
                session,
                since=_parse_date(args.from_date),
                until=_parse_date(args.to_date),
                company_id=args.company_id,
                offsets=offsets or None,
                rule_version=rule_version,
                dry_run=args.dry_run,
            )
            print(json.dumps(stats.as_dict(), indent=2))
        elif args.action == "expire":
            print(json.dumps({"fired": expire_triggers(session)}, indent=2))
        else:
            rows = upcoming_triggers_report(
                session,
                within_days=args.within_days,
                min_confidence=args.min_confidence,
                trigger_types=args.types,
            )
            print(f"\n### Trigger Calendar — next {args.within_days} days\n")
            print(markdown_table(rows, list(CALENDAR_COLUMNS)))
            print()
            if args.digest:
                print(calendar_digest(rows))
            if args.csv:
                print(f"Wrote {len(rows)} rows to {write_csv(rows, args.csv, CALENDAR_COLUMNS)}")
    return 0


def cmd_alerts(args: argparse.Namespace) -> int:
    config = bootstrap(args)
    pipeline = MonitorPipeline(config)
    with session_scope() as session:
        if args.action == "flush":
            print(json.dumps(pipeline.dispatcher.flush(session), indent=2))
        else:
            pending = repo.pending_alerts(session)
            print(f"{len(pending)} pending alert(s)")
            for alert in pending[:50]:
                print(f"  [{alert.priority}] {alert.channel}: {alert.title}")
    return 0


def cmd_classify(args: argparse.Namespace) -> int:
    """Classify ad-hoc text without touching the database — the debugging path."""
    config = Config.load(getattr(args, "config", None))
    setup_logging(level="WARNING")
    from .classifier.rules import RuleClassifier
    from .classifier.scoring import ScoreInput, scorer_from_config
    from .config import load_investors
    from .parser.entities import InvestorMatcher, extract_all

    text = args.text if args.text else sys.stdin.read()
    classification = RuleClassifier().classify(
        text, float(config.get("classifier.min_confidence", 0.35))
    )
    extraction = extract_all(text, matcher=InvestorMatcher(load_investors()))
    result = scorer_from_config(config).score(
        ScoreInput(
            category=classification.category,
            confidence=classification.confidence,
            certainty=classification.certainty,
            amount_inr=extraction.amount_inr,
            percent_of_equity=extraction.percent_of_equity,
            marquee_investor=extraction.marquee_investor,
            promoter_involved=extraction.promoter_involved,
            lock_in_expiry_date=extraction.lock_in_expiry_date,
            filing_date=dt.date.today(),
        )
    )
    print(
        json.dumps(
            {
                "classification": classification.to_dict(),
                "extraction": extraction.to_dict(),
                "score": result.score,
                "priority": result.priority,
                "breakdown": result.breakdown,
            },
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_train_model(args: argparse.Namespace) -> int:
    """Weak supervision: train the ML layer on rule-labelled filings."""
    config = bootstrap(args)
    from .classifier.ml import MLClassifier

    with session_scope() as session:
        stmt = select(EventFiling).where(
            EventFiling.category != "Other",
            EventFiling.classification_confidence >= args.min_confidence,
        )
        filings = list(session.scalars(stmt))
        texts = [f.search_text or f.headline for f in filings]
        labels = [f.category for f in filings]

    if len(texts) < args.min_samples:
        print(
            f"Only {len(texts)} labelled filings (need {args.min_samples}); "
            "keep running rules-only until the corpus grows."
        )
        return 1

    classifier = MLClassifier()
    stats = classifier.train(texts, labels)
    path = classifier.save(config.get("classifier.ml_model_path"))
    print(json.dumps({**stats, "path": str(path)}, indent=2))
    print("Set classifier.use_ml_model: true to enable blending.")
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    config = bootstrap(args)
    from .scheduler import build_scheduler, crontab_lines

    if args.print_cron:
        print(crontab_lines(config))
        return 0

    create_all()
    pipeline = MonitorPipeline(config)

    def job(days: int = 1) -> None:
        with session_scope() as session:
            stats = pipeline.run(
                session, from_date=dt.date.today() - dt.timedelta(days=days), source="BSE"
            )
        log.info("Scheduled run complete", extra=stats.as_dict())

    scheduler = build_scheduler(config, job)
    if scheduler is None:
        print("APScheduler unavailable. Equivalent crontab:\n")
        print(crontab_lines(config))
        return 1
    print("Scheduler running (Ctrl-C to stop).")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("\nScheduler stopped.")
    return 0


def cmd_seed_demo(args: argparse.Namespace) -> int:
    """Load the bundled sample filings so the whole pipeline can be exercised offline."""
    config = bootstrap(args)
    create_all()
    path = Path(args.file or Path(__file__).parent / "examples" / "sample_filings.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    raws: List[RawFiling] = []
    for item in payload:
        filing_dt = item.get("filing_datetime")
        raws.append(
            RawFiling(
                source=item.get("source", "BSE"),
                source_filing_id=item.get("source_filing_id"),
                company_name=item["company_name"],
                bse_code=item.get("bse_code"),
                headline=item.get("headline", ""),
                body_text=item.get("body_text", ""),
                filing_datetime=dt.datetime.fromisoformat(filing_dt) if filing_dt else None,
                raw_payload={"demo": True},
            )
        )

    # PDFs are disabled for the demo: the sample filings carry their text inline.
    config.data.setdefault("pdf", {})["enabled"] = False
    pipeline = MonitorPipeline(config)
    with session_scope() as session:
        stats, processed = pipeline.process_batch(session, raws, send_alerts=not args.no_alerts)
        print(json.dumps(stats.as_dict(), indent=2))
        rows = [
            {
                "date": f.filing_date.isoformat() if f.filing_date else "",
                "company": f.company.name if f.company else "",
                "event_type": f.category,
                "score": f.opportunity_score,
                "priority": f.priority,
            }
            for f in sorted(processed, key=lambda x: -x.opportunity_score)
        ]
        print()
        print(markdown_table(rows, ["date", "company", "event_type", "score", "priority"]))
    return 0


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bse-monitor", description="BSE corporate filings monitoring system"
    )
    parser.add_argument("--config", help="path to config.yaml")
    parser.add_argument("--log-level", help="override app.log_level")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create tables").set_defaults(func=cmd_init_db)

    run_parser = sub.add_parser("run", help="scrape, classify and alert")
    run_parser.add_argument("--from", dest="from_date", help="YYYY-MM-DD")
    run_parser.add_argument("--to", dest="to_date", help="YYYY-MM-DD")
    run_parser.add_argument("--days", type=int, default=0, help="window size ending today")
    run_parser.add_argument(
        "--sources", nargs="+", default=["BSE"], choices=["BSE", "NSE", "SEBI"]
    )
    run_parser.add_argument("--limit", type=int, help="stop after N filings (smoke tests)")
    run_parser.add_argument("--no-alerts", action="store_true")
    run_parser.set_defaults(func=cmd_run)

    backfill = sub.add_parser("backfill", help="re-cover a historical window day by day")
    backfill.add_argument("--from", dest="from_date")
    backfill.add_argument("--to", dest="to_date")
    backfill.add_argument("--days", type=int, default=7)
    backfill.add_argument("--source", default="BSE", choices=["BSE", "NSE", "SEBI"])
    backfill.set_defaults(func=cmd_backfill)

    report = sub.add_parser("report", help="print a reporting view")
    report.add_argument(
        "kind", choices=["daily", "fundraise", "selldown", "watchlist", "calendar"]
    )
    report.add_argument("--date", help="YYYY-MM-DD (daily only)")
    report.add_argument("--min-score", type=int, default=0)
    report.add_argument("--csv", help="also write the rows to this path")
    report.add_argument("--within-days", type=int, default=90, help="calendar horizon")
    report.add_argument("--min-confidence", type=float, default=0.0)
    report.set_defaults(func=cmd_report)

    calendar = sub.add_parser("calendar", help="forward deadline calendar")
    calendar.add_argument("action", choices=["refresh", "list", "expire"])
    calendar.add_argument("--from", dest="from_date", help="YYYY-MM-DD")
    calendar.add_argument("--to", dest="to_date", help="YYYY-MM-DD")
    calendar.add_argument("--company-id", type=int)
    calendar.add_argument("--rule-version")
    calendar.add_argument("--within-days", type=int, default=90)
    calendar.add_argument("--min-confidence", type=float, default=0.0)
    calendar.add_argument("--types", nargs="+", help="filter to these trigger types")
    calendar.add_argument("--digest", action="store_true", help="also print the bucketed digest")
    calendar.add_argument("--csv")
    calendar.add_argument("--dry-run", action="store_true")
    calendar.set_defaults(func=cmd_calendar)

    alerts = sub.add_parser("alerts", help="inspect or flush the alert queue")
    alerts.add_argument("action", choices=["list", "flush"])
    alerts.set_defaults(func=cmd_alerts)

    classify = sub.add_parser("classify", help="classify and score ad-hoc text")
    classify.add_argument("text", nargs="?", help="text (reads stdin when omitted)")
    classify.set_defaults(func=cmd_classify)

    train = sub.add_parser("train-model", help="train the optional ML classifier")
    train.add_argument("--min-samples", type=int, default=200)
    train.add_argument("--min-confidence", type=float, default=0.6)
    train.set_defaults(func=cmd_train_model)

    schedule = sub.add_parser("schedule", help="run the scheduler")
    schedule.add_argument("--print-cron", action="store_true")
    schedule.set_defaults(func=cmd_schedule)

    seed = sub.add_parser("seed-demo", help="load bundled sample filings")
    seed.add_argument("--file", help="path to a filings JSON file")
    seed.add_argument("--no-alerts", action="store_true")
    seed.set_defaults(func=cmd_seed_demo)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:
        logging.getLogger("bse_monitor").exception("Command failed")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

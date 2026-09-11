"""APScheduler-based scheduling.

Three jobs, mirroring how BSE actually disseminates:

* ``intraday``      — short window, every 15 minutes through the trading day.
* ``evening_sweep`` — wider window after close, catching post-market filings.
* ``backfill``      — weekly reconciliation over the last 7 days, which repairs
  anything a failed intraday run missed. Because ingestion is idempotent this
  simply no-ops over filings already stored.

APScheduler is optional: ``main.py schedule`` prints the equivalent crontab and
exits if it is not installed, so the container can fall back to system cron.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)


def crontab_lines(config: Any) -> str:
    """Equivalent crontab, for deployments that prefer system cron."""
    cfg = config.section("scheduler")
    entrypoint = "cd /app && python -m bse_monitor.main"
    return "\n".join(
        [
            f"{cfg.get('intraday_cron', '*/15 6-16 * * 1-5')} {entrypoint} run >> /var/log/bse_intraday.log 2>&1",
            f"{cfg.get('evening_sweep_cron', '30 16 * * 1-5')} {entrypoint} run --days 2 >> /var/log/bse_evening.log 2>&1",
            f"{cfg.get('backfill_cron', '0 2 * * 6')} {entrypoint} backfill --days 7 >> /var/log/bse_backfill.log 2>&1",
        ]
    )


def build_scheduler(config: Any, run_callable: Callable[..., Any]) -> Optional[Any]:
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.warning("APScheduler not installed; use the crontab from `main.py schedule --print-cron`")
        return None

    cfg = config.section("scheduler")
    scheduler = BlockingScheduler(timezone="UTC")

    jobs: Dict[str, Dict[str, Any]] = {
        "intraday": {"cron": cfg.get("intraday_cron", "*/15 6-16 * * 1-5"), "kwargs": {"days": 1}},
        "evening_sweep": {"cron": cfg.get("evening_sweep_cron", "30 16 * * 1-5"), "kwargs": {"days": 2}},
        "backfill": {"cron": cfg.get("backfill_cron", "0 2 * * 6"), "kwargs": {"days": 7}},
    }
    for name, spec in jobs.items():
        scheduler.add_job(
            run_callable,
            CronTrigger.from_crontab(spec["cron"], timezone="UTC"),
            id=name,
            name=name,
            kwargs=spec["kwargs"],
            # A long run must not spawn a second one on top of itself; a missed
            # tick is harmless because the next window overlaps.
            max_instances=1,
            coalesce=True,
            misfire_grace_time=600,
        )
        log.info("Job scheduled", extra={"job": name, "cron": spec["cron"]})
    return scheduler


def next_run_preview(config: Any, count: int = 3) -> list[str]:  # pragma: no cover - convenience
    try:
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        return []
    cron = config.get("scheduler.intraday_cron", "*/15 6-16 * * 1-5")
    trigger = CronTrigger.from_crontab(cron, timezone="UTC")
    previews: list[str] = []
    moment = dt.datetime.now(dt.timezone.utc)
    for _ in range(count):
        moment = trigger.get_next_fire_time(None, moment)
        if moment is None:
            break
        previews.append(moment.isoformat())
        moment = moment + dt.timedelta(seconds=1)
    return previews

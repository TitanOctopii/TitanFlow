"""TitanFlow Scheduler — async task scheduling via APScheduler."""

from __future__ import annotations

import logging
from typing import Any, Callable, Coroutine

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger("titanflow.scheduler")

AsyncJob = Callable[..., Coroutine[Any, Any, None]]


class Scheduler:
    """Async task scheduler wrapping APScheduler."""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        """Start the scheduler."""
        self._scheduler.start()
        logger.info("Scheduler started")

    def shutdown(self) -> None:
        """Shutdown the scheduler."""
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")

    def add_interval(
        self,
        job_id: str,
        func: AsyncJob,
        seconds: int | None = None,
        minutes: int | None = None,
        hours: int | None = None,
        **kwargs: Any,
    ) -> None:
        """Schedule a recurring job at a fixed interval."""
        interval_kwargs = {}
        if seconds is not None:
            interval_kwargs["seconds"] = seconds
        if minutes is not None:
            interval_kwargs["minutes"] = minutes
        if hours is not None:
            interval_kwargs["hours"] = hours
        trigger = IntervalTrigger(**interval_kwargs)
        self._scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            **kwargs,
        )
        logger.info(f"Scheduled interval job: {job_id}")

    def add_cron(
        self,
        job_id: str,
        func: AsyncJob,
        *,
        hour: int | str | None = None,
        minute: int | str | None = None,
        day_of_week: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Schedule a job using cron-like timing."""
        trigger = CronTrigger(
            hour=hour,
            minute=minute,
            day_of_week=day_of_week,
            timezone="US/Eastern",
        )
        self._scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            **kwargs,
        )
        logger.info(f"Scheduled cron job: {job_id}")

    def remove_job(self, job_id: str) -> None:
        """Remove a scheduled job."""
        try:
            self._scheduler.remove_job(job_id)
            logger.info(f"Removed job: {job_id}")
        except Exception:
            logger.warning(f"Job not found: {job_id}")

    def list_jobs(self) -> list[dict[str, Any]]:
        """List all scheduled jobs."""
        jobs = self._scheduler.get_jobs()
        return [
            {
                "id": job.id,
                "next_run": str(job.next_run_time),
                "trigger": str(job.trigger),
            }
            for job in jobs
        ]

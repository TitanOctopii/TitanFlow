import pytest

from titanflow.core.scheduler import Scheduler


@pytest.mark.asyncio
async def test_scheduler_adds_jobs():
    scheduler = Scheduler()
    scheduler.start()

    async def _noop():
        return None

    scheduler.add_interval("test.interval", _noop, seconds=60)
    scheduler.add_cron("test.cron", _noop, hour=3, minute=15, day_of_week="mon")

    jobs = scheduler.list_jobs()
    job_ids = {j["id"] for j in jobs}

    assert "test.interval" in job_ids
    assert "test.cron" in job_ids

    scheduler.shutdown()

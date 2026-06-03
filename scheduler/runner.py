from __future__ import annotations

from typing import Callable

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    _OK = True
except ImportError:
    _OK = False

from core.models import JobConfig


class JobScheduler:
    def __init__(self):
        if not _OK:
            raise RuntimeError("apscheduler not installed. Run: pip install apscheduler")
        self._sched = AsyncIOScheduler()

    def add(self, job: JobConfig, fn: Callable):
        if not job.schedule:
            raise ValueError(f"Job '{job.name}' has no 'schedule' cron expression.")
        self._sched.add_job(
            fn,
            CronTrigger.from_crontab(job.schedule),
            id=job.name,
            name=job.name,
            replace_existing=True,
        )

    def start(self):
        self._sched.start()

    def stop(self):
        self._sched.shutdown(wait=False)

    def list_jobs(self) -> list[dict]:
        return [
            {"id": j.id, "name": j.name, "next_run": str(j.next_run_time)}
            for j in self._sched.get_jobs()
        ]

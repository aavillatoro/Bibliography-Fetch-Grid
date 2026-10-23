import logging
import threading
from contextlib import contextmanager
from datetime import timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app import db, jobs
from app.models import Job
from app.schemas import JobRequest, JobStatus

logger = logging.getLogger(__name__)

POLL_INTERVAL = 1.0
HEARTBEAT_INTERVAL = 10.0
STALE_AFTER = timedelta(seconds=60)
MAX_ATTEMPTS = 3
SHUTDOWN_TIMEOUT = 30.0


def requeue_stale_jobs(session: Session) -> None:
    # a running job whose heartbeat stopped belongs to a process that died.
    # heartbeat_at is null only on jobs claimed before the column existed.
    stale = and_(
        Job.status.in_((JobStatus.FETCHING, JobStatus.PROCESSING)),
        or_(Job.heartbeat_at.is_(None), Job.heartbeat_at < func.now() - STALE_AFTER),
    )
    failed = session.execute(
        update(Job)
        .where(stale, Job.attempts >= MAX_ATTEMPTS)
        .values(
            status=JobStatus.FAILED,
            completed_at=jobs.now(),
            error=f"interrupted {MAX_ATTEMPTS} times by a crash or restart, giving up",
        )
    ).rowcount
    requeued = session.execute(update(Job).where(stale).values(status=JobStatus.QUEUED)).rowcount
    session.commit()
    if failed or requeued:
        logger.warning("requeued %d stale jobs, failed %d", requeued, failed)


def claim_job(session: Session) -> tuple[UUID, JobRequest] | None:
    # skip locked lets several api processes poll without taking the same job
    job = session.scalar(
        select(Job)
        .where(Job.status == JobStatus.QUEUED)
        .order_by(Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None

    request = JobRequest(
        query=job.query,
        from_year=job.from_year,
        to_year=job.to_year,
        min_citations=job.min_citations,
        limit=job.limit,
        sort=job.sort,
    )
    session.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(status=JobStatus.FETCHING, attempts=Job.attempts + 1, heartbeat_at=func.now())
    )
    session.commit()
    return job.id, request


@contextmanager
def heartbeat(job_id: UUID):
    stop = threading.Event()

    def beat():
        while not stop.wait(HEARTBEAT_INTERVAL):
            try:
                with db.SessionLocal() as session:
                    session.execute(
                        update(Job).where(Job.id == job_id).values(heartbeat_at=func.now())
                    )
                    session.commit()
            except Exception:
                logger.exception("heartbeat for job %s failed", job_id)

    thread = threading.Thread(target=beat, name=f"heartbeat-{job_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


def run_once() -> bool:
    with db.SessionLocal() as session:
        requeue_stale_jobs(session)
        claimed = claim_job(session)
    if claimed is None:
        return False

    job_id, request = claimed
    with heartbeat(job_id):
        jobs.process_job(job_id, request)
    return True


class Worker:
    def __init__(self):
        self._stop = threading.Event()
        # daemon so a job still running after the shutdown timeout can't block exit
        self._thread = threading.Thread(target=self._run, name="job-worker", daemon=True)

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if not self.running:
            return
        self._thread.join(SHUTDOWN_TIMEOUT)
        if self.running:
            logger.warning("job still running at shutdown, it will be requeued when stale")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                busy = run_once()
            except Exception:
                logger.exception("job worker step failed")
                busy = False
            if not busy:
                self._stop.wait(POLL_INTERVAL)

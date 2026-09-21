import threading
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app import openalex
from app.schemas import JobDetail, JobRequest, JobStatus

# in-memory until postgres lands; background tasks run in a threadpool
_jobs: dict[UUID, JobDetail] = {}
_lock = threading.Lock()


def now() -> datetime:
    return datetime.now(timezone.utc)


def create_job(request: JobRequest) -> JobDetail:
    job = JobDetail(
        id=uuid4(),
        status=JobStatus.QUEUED,
        created_at=now(),
        **request.model_dump(),
    )
    with _lock:
        _jobs[job.id] = job
    return job


def get_job(job_id: UUID) -> JobDetail | None:
    with _lock:
        return _jobs.get(job_id)


def list_jobs() -> list[JobDetail]:
    with _lock:
        return sorted(_jobs.values(), key=lambda job: job.created_at, reverse=True)


def update_job(job_id: UUID, **fields) -> None:
    with _lock:
        _jobs[job_id] = _jobs[job_id].model_copy(update=fields)


def process_job(job_id: UUID, request: JobRequest) -> None:
    update_job(job_id, status=JobStatus.FETCHING, started_at=now())

    try:
        works = openalex.fetch_works(request)

        update_job(job_id, status=JobStatus.PROCESSING)
        papers = [openalex.normalize_work(work) for work in works]

        update_job(
            job_id,
            status=JobStatus.COMPLETED,
            completed_at=now(),
            papers_found=len(papers),
            results=papers,
        )
    except Exception as e:
        update_job(
            job_id,
            status=JobStatus.FAILED,
            completed_at=now(),
            error=f"{type(e).__name__}: {e}",
        )

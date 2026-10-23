import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from app import db, openalex, schemas
from app.models import Job, JobPaper, Paper
from app.schemas import JobRequest, JobStatus

logger = logging.getLogger(__name__)

def now() -> datetime:
    return datetime.now(timezone.utc)


def new_job(request: JobRequest, saved_search_id: UUID | None = None) -> Job:
    return Job(
        status=JobStatus.QUEUED,
        created_at=now(),
        saved_search_id=saved_search_id,
        **request.model_dump(),
    )


def create_job(
    session: Session, request: JobRequest, saved_search_id: UUID | None = None
) -> Job:
    job = new_job(request, saved_search_id)
    session.add(job)
    session.commit()
    return job


def request_from(row) -> JobRequest:
    # jobs and saved searches store the same request columns
    return JobRequest.model_validate(row, from_attributes=True)


def get_job(session: Session, job_id: UUID) -> Job | None:
    return session.scalar(
        select(Job).where(Job.id == job_id).options(selectinload(Job.job_papers))
    )


def list_jobs(
    session: Session, status: JobStatus | None, limit: int, offset: int
) -> list[Job]:
    query = select(Job).order_by(Job.created_at.desc()).limit(limit).offset(offset)
    if status is not None:
        query = query.where(Job.status == status)
    return list(session.scalars(query))


def dedupe(papers: list[schemas.Paper]) -> list[schemas.Paper]:
    seen = set()
    unique = []
    for paper in papers:
        if paper.source_id not in seen:
            seen.add(paper.source_id)
            unique.append(paper)
    return unique


def upsert_papers(session: Session, papers: list[schemas.Paper]) -> dict[str, int]:
    if not papers:
        return {}

    stmt = insert(Paper).values([paper.model_dump() for paper in papers])
    stmt = stmt.on_conflict_do_update(
        index_elements=[Paper.source_id],
        set_={
            "doi": stmt.excluded.doi,
            "title": stmt.excluded.title,
            "publication_year": stmt.excluded.publication_year,
            "cited_by_count": stmt.excluded.cited_by_count,
            "authors": stmt.excluded.authors,
            "work_type": stmt.excluded.work_type,
            "venue": stmt.excluded.venue,
            "abstract": stmt.excluded.abstract,
            "open_access_url": stmt.excluded.open_access_url,
            "updated_at": func.now(),
        },
    ).returning(Paper.source_id, Paper.id)

    return dict(session.execute(stmt).tuples().all())


def save_results(session: Session, job_id: UUID, papers: list[schemas.Paper]) -> None:
    paper_ids = upsert_papers(session, papers)

    # replace rather than append so re-running a job can't duplicate results
    session.execute(delete(JobPaper).where(JobPaper.job_id == job_id))
    session.add_all(
        JobPaper(job_id=job_id, paper_id=paper_ids[paper.source_id], rank=rank)
        for rank, paper in enumerate(papers, start=1)
    )

    session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(status=JobStatus.COMPLETED, completed_at=now(), papers_found=len(papers))
    )


def set_status(session: Session, job_id: UUID, status: JobStatus, **fields) -> None:
    session.execute(update(Job).where(Job.id == job_id).values(status=status, **fields))
    session.commit()


def process_job(job_id: UUID, request: JobRequest) -> None:
    with db.SessionLocal() as session:
        try:
            set_status(session, job_id, JobStatus.FETCHING, started_at=now())
            works = openalex.fetch_works(request)

            set_status(session, job_id, JobStatus.PROCESSING)
            papers = dedupe([openalex.normalize_work(work) for work in works])

            save_results(session, job_id, papers)
            session.commit()
        except Exception as e:
            logger.exception("job %s failed", job_id)
            session.rollback()
            set_status(
                session,
                job_id,
                JobStatus.FAILED,
                completed_at=now(),
                error=f"{type(e).__name__}: {e}",
            )

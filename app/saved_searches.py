import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app import jobs
from app.models import Job, JobPaper, Paper, SavedSearch
from app.schemas import (
    JobStatus,
    JobSummary,
    SavedSearchDetail,
    SavedSearchDiff,
    SavedSearchRequest,
    SavedSearchSummary,
)
from app.schemas import Paper as PaperSchema

logger = logging.getLogger(__name__)

PERIODS = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1)}
RUNS_SHOWN = 20


class NameTaken(Exception):
    pass


def create_saved_search(session: Session, request: SavedSearchRequest) -> SavedSearch:
    search = SavedSearch(**request.model_dump())
    if request.schedule:
        # due right away, so the first run is there for the next one to diff against
        search.next_run_at = jobs.now()
    session.add(search)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise NameTaken
    return search


def get_saved_search(session: Session, search_id: UUID) -> SavedSearch | None:
    return session.get(SavedSearch, search_id)


def list_saved_searches(session: Session) -> list[SavedSearch]:
    return list(session.scalars(select(SavedSearch).order_by(SavedSearch.name)))


def delete_saved_search(session: Session, search: SavedSearch) -> None:
    session.delete(search)
    session.commit()


def run_saved_search(session: Session, search: SavedSearch) -> Job:
    return jobs.create_job(session, jobs.request_from(search), saved_search_id=search.id)


def enqueue_due_searches(session: Session) -> int:
    # skip locked leaves a search to whichever worker locked it first. the job
    # insert and the next_run_at bump commit together, so once that worker
    # commits the search is no longer due. the caller commits.
    due = session.scalars(
        select(SavedSearch)
        .where(SavedSearch.next_run_at <= func.now())
        .with_for_update(skip_locked=True)
    ).all()
    for search in due:
        session.add(jobs.new_job(jobs.request_from(search), saved_search_id=search.id))
        # count from now, so runs missed while the server was down collapse into one
        search.next_run_at = func.now() + PERIODS[search.schedule]
    session.flush()
    if due:
        logger.info("enqueued %d scheduled searches", len(due))
    return len(due)


def recent_runs(session: Session, search_id: UUID, limit: int, completed=False) -> list[Job]:
    query = (
        select(Job)
        .where(Job.saved_search_id == search_id)
        .order_by(Job.created_at.desc())
        .limit(limit)
    )
    if completed:
        query = query.where(Job.status == JobStatus.COMPLETED)
    return list(session.scalars(query))


def new_papers_query(latest_id: UUID, previous_id: UUID):
    # papers.source_id is unique, so matching paper ids matches openalex ids
    previous = aliased(JobPaper)
    return (
        select(Paper)
        .join(JobPaper, JobPaper.paper_id == Paper.id)
        .where(
            JobPaper.job_id == latest_id,
            JobPaper.paper_id.not_in(
                select(previous.paper_id).where(previous.job_id == previous_id)
            ),
        )
        .order_by(JobPaper.rank)
    )


def diff(session: Session, search_id: UUID) -> SavedSearchDiff:
    runs = recent_runs(session, search_id, 2, completed=True)
    latest = runs[0].id if runs else None
    if len(runs) < 2:
        return SavedSearchDiff(latest_job_id=latest, previous_job_id=None, papers=[])
    papers = session.scalars(new_papers_query(latest, runs[1].id))
    return SavedSearchDiff(
        latest_job_id=latest,
        previous_job_id=runs[1].id,
        papers=[PaperSchema.model_validate(paper) for paper in papers],
    )


def count_new(session: Session, search_id: UUID) -> int:
    runs = recent_runs(session, search_id, 2, completed=True)
    if len(runs) < 2:
        return 0
    query = new_papers_query(runs[0].id, runs[1].id).subquery()
    return session.scalar(select(func.count()).select_from(query))


def summarize(
    session: Session, search: SavedSearch, schema=SavedSearchSummary, runs_shown=1
) -> SavedSearchSummary:
    runs = [JobSummary.model_validate(job) for job in recent_runs(session, search.id, runs_shown)]
    summary = schema.model_validate(search)
    summary.latest_job = runs[0] if runs else None
    summary.new_since_last_run = count_new(session, search.id)
    if isinstance(summary, SavedSearchDetail):
        summary.runs = runs
    return summary


def detail(session: Session, search: SavedSearch) -> SavedSearchDetail:
    return summarize(session, search, SavedSearchDetail, RUNS_SHOWN)

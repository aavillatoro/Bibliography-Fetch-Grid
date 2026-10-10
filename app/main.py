import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import db, export, jobs
from app.schemas import (
    ExportFormat,
    JobDetail,
    JobRequest,
    JobStatus,
    JobSummary,
    Paper,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    with db.SessionLocal() as session:
        interrupted = jobs.fail_interrupted_jobs(session)
    if interrupted:
        logger.warning("marked %d interrupted jobs as failed", interrupted)
    yield


app = FastAPI(title="Bibliography Fetch Grid", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/jobs", response_model=JobSummary, status_code=202)
def create_job(
    request: JobRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(db.get_session),
):
    job = jobs.create_job(session, request)
    background_tasks.add_task(jobs.process_job, job.id, request)
    return job


@app.get("/jobs", response_model=list[JobSummary])
def list_jobs(
    status: JobStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(db.get_session),
):
    return jobs.list_jobs(session, status, limit, offset)


@app.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: UUID, session: Session = Depends(db.get_session)):
    job = jobs.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/jobs/{job_id}/export")
def export_job(
    job_id: UUID,
    format: ExportFormat = "bibtex",
    session: Session = Depends(db.get_session),
):
    job = jobs.get_job(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Job has not completed")

    papers = [Paper.model_validate(paper) for paper in job.results]
    media_type, _ = export.MEDIA_TYPES[format]
    return Response(
        export.render(format, papers),
        media_type=f"{media_type}; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename(job.query, format)}"'
        },
    )


@app.get("/health")
def health(session: Session = Depends(db.get_session)):
    session.execute(text("select 1"))
    return {"status": "ok"}

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import collections, db, export, jobs
from app.config import settings
from app.models import Collection
from app.schemas import (
    CollectionDetail,
    CollectionPapersRequest,
    CollectionRequest,
    CollectionSummary,
    ExportFormat,
    JobDetail,
    JobRequest,
    JobStatus,
    JobSummary,
    Paper,
)
from app.worker import Worker

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.worker = Worker()
    if settings.run_worker:
        app.state.worker.start()
    yield
    app.state.worker.stop()


app = FastAPI(title="Bibliography Fetch Grid", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/jobs", response_model=JobSummary, status_code=202)
def create_job(
    request: JobRequest,
    session: Session = Depends(db.get_session),
):
    return jobs.create_job(session, request)


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

    return export_response(job.query, format, job.results)


def export_response(name: str, format: ExportFormat, papers: list) -> Response:
    media_type, _ = export.MEDIA_TYPES[format]
    return Response(
        export.render(format, [Paper.model_validate(paper) for paper in papers]),
        media_type=f"{media_type}; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{export.filename(name, format)}"'
        },
    )


def require_collection(
    collection_id: UUID, session: Session = Depends(db.get_session)
) -> Collection:
    collection = collections.get_collection(session, collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return collection


NAME_TAKEN = HTTPException(
    status_code=409, detail="A collection with that name already exists"
)


@app.post("/collections", response_model=CollectionSummary, status_code=201)
def create_collection(
    request: CollectionRequest, session: Session = Depends(db.get_session)
):
    try:
        return collections.create_collection(session, request.name)
    except collections.NameTaken:
        raise NAME_TAKEN


@app.get("/collections", response_model=list[CollectionSummary])
def list_collections(session: Session = Depends(db.get_session)):
    return collections.list_collections(session)


@app.get("/collections/{collection_id}", response_model=CollectionDetail)
def get_collection(collection: Collection = Depends(require_collection)):
    return collection


@app.patch("/collections/{collection_id}", response_model=CollectionSummary)
def rename_collection(
    request: CollectionRequest,
    collection: Collection = Depends(require_collection),
    session: Session = Depends(db.get_session),
):
    try:
        return collections.rename_collection(session, collection, request.name)
    except collections.NameTaken:
        raise NAME_TAKEN


@app.delete("/collections/{collection_id}", status_code=204)
def delete_collection(
    collection: Collection = Depends(require_collection),
    session: Session = Depends(db.get_session),
):
    collections.delete_collection(session, collection)


@app.post("/collections/{collection_id}/papers", response_model=CollectionSummary)
def add_collection_papers(
    request: CollectionPapersRequest,
    collection: Collection = Depends(require_collection),
    session: Session = Depends(db.get_session),
):
    try:
        collections.add_papers(session, collection.id, request.source_ids)
    except collections.UnknownPapers as e:
        raise HTTPException(
            status_code=404, detail=f"Unknown papers: {', '.join(e.source_ids)}"
        )
    return collections.get_collection(session, collection.id)


@app.delete("/collections/{collection_id}/papers", response_model=CollectionSummary)
def remove_collection_papers(
    source_id: list[str] = Query(min_length=1),
    collection: Collection = Depends(require_collection),
    session: Session = Depends(db.get_session),
):
    collections.remove_papers(session, collection.id, source_id)
    return collections.get_collection(session, collection.id)


@app.get("/collections/{collection_id}/export")
def export_collection(
    format: ExportFormat = "bibtex",
    collection: Collection = Depends(require_collection),
):
    return export_response(collection.name, format, collection.papers)


@app.get("/health")
def health(session: Session = Depends(db.get_session)):
    session.execute(text("select 1"))
    return {"status": "ok"}

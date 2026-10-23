from datetime import datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    FETCHING = "FETCHING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


SortOrder = Literal["citations_desc", "year_desc", "relevance"]
Schedule = Literal["daily", "weekly"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class JobRequest(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=500,
        description='matched against title and abstract; wrap a phrase in double quotes for an exact match',
    )
    from_year: int | None = None
    to_year: int | None = None
    min_citations: int | None = Field(default=None, ge=0)
    limit: int = Field(default=25, ge=1, le=500)
    sort: SortOrder = "relevance"

    @field_validator("query")
    @classmethod
    def check_query_has_terms(cls, query: str) -> str:
        if not query.replace(",", " ").strip():
            raise ValueError("query must contain search terms")
        return query.strip()

    @model_validator(mode="after")
    def check_year_range(self):
        if self.from_year is not None and self.to_year is not None and self.from_year > self.to_year:
            raise ValueError("from_year must be less than or equal to to_year")
        return self


class Paper(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_id: str
    doi: str | None
    title: str | None
    publication_year: int | None
    cited_by_count: int
    authors: list[str]
    work_type: str | None = None
    venue: str | None = None
    abstract: str | None = None
    open_access_url: str | None = None


ExportFormat = Literal["bibtex", "ris", "csv"]


class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    query: str
    from_year: int | None
    to_year: int | None
    min_citations: int | None = None
    limit: int
    sort: SortOrder
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    papers_found: int | None = None
    error: str | None = None
    saved_search_id: UUID | None = None


class JobDetail(JobSummary):
    results: list[Paper] = []


class CollectionRequest(BaseModel):
    name: Name


class CollectionPapersRequest(BaseModel):
    source_ids: list[str] = Field(min_length=1, max_length=200)


class CollectionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime
    paper_count: int


class CollectionDetail(CollectionSummary):
    papers: list[Paper] = []


class SavedSearchRequest(JobRequest):
    name: Name
    schedule: Schedule | None = None


class SavedSearchSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    query: str
    from_year: int | None
    to_year: int | None
    min_citations: int | None
    limit: int
    sort: SortOrder
    schedule: Schedule | None
    next_run_at: datetime | None
    created_at: datetime
    latest_job: JobSummary | None = None
    new_since_last_run: int = 0


class SavedSearchDetail(SavedSearchSummary):
    runs: list[JobSummary] = []


class SavedSearchDiff(BaseModel):
    latest_job_id: UUID | None
    previous_job_id: UUID | None
    papers: list[Paper]

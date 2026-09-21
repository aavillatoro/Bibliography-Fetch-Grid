from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class JobStatus(str, Enum):
    QUEUED = "QUEUED"
    FETCHING = "FETCHING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


SortOrder = Literal["citations_desc", "year_desc", "relevance"]


class JobRequest(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=500,
        description='matched against title and abstract; wrap a phrase in double quotes for an exact match',
    )
    from_year: int | None = None
    to_year: int | None = None
    # one upstream page until pagination lands
    limit: int = Field(default=25, ge=1, le=200)
    sort: SortOrder = "citations_desc"

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


class JobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    query: str
    from_year: int | None
    to_year: int | None
    limit: int
    sort: SortOrder
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    papers_found: int | None = None
    error: str | None = None


class JobDetail(JobSummary):
    results: list[Paper] = []

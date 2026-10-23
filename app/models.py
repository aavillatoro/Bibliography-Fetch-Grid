import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    column_property,
    mapped_column,
    relationship,
)

from app.schemas import JobStatus


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    query: Mapped[str] = mapped_column(Text)
    from_year: Mapped[int | None]
    to_year: Mapped[int | None]
    min_citations: Mapped[int | None]
    limit: Mapped[int] = mapped_column("requested_limit", Integer)
    sort: Mapped[str] = mapped_column(String(20))
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=20), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    papers_found: Mapped[int | None]
    error: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # set null so deleting a saved search keeps its past runs in the job history
    saved_search_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("saved_searches.id", ondelete="SET NULL"), index=True
    )

    job_papers: Mapped[list["JobPaper"]] = relationship(
        back_populates="job",
        order_by="JobPaper.rank",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def results(self) -> list["Paper"]:
        return [job_paper.paper for job_paper in self.job_papers]


class SavedSearch(Base):
    __tablename__ = "saved_searches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    query: Mapped[str] = mapped_column(Text)
    from_year: Mapped[int | None]
    to_year: Mapped[int | None]
    min_citations: Mapped[int | None]
    limit: Mapped[int] = mapped_column("requested_limit", Integer)
    sort: Mapped[str] = mapped_column(String(20))
    schedule: Mapped[str | None] = mapped_column(String(10))
    # null when unscheduled
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, unique=True)
    doi: Mapped[str | None] = mapped_column(Text, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    publication_year: Mapped[int | None]
    cited_by_count: Mapped[int]
    authors: Mapped[list[str]] = mapped_column(ARRAY(Text))
    work_type: Mapped[str | None] = mapped_column(String(40))
    venue: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    open_access_url: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JobPaper(Base):
    __tablename__ = "job_papers"
    __table_args__ = (UniqueConstraint("job_id", "rank"),)

    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id"), primary_key=True, index=True
    )
    rank: Mapped[int]

    job: Mapped[Job] = relationship(back_populates="job_papers")
    paper: Mapped[Paper] = relationship(lazy="joined")


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    collection_papers: Mapped[list["CollectionPaper"]] = relationship(
        order_by=lambda: (CollectionPaper.added_at, CollectionPaper.paper_id),
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def papers(self) -> list["Paper"]:
        return [collection_paper.paper for collection_paper in self.collection_papers]


class CollectionPaper(Base):
    __tablename__ = "collection_papers"

    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id"), primary_key=True, index=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    paper: Mapped[Paper] = relationship(lazy="joined")


Collection.paper_count = column_property(
    select(func.count(CollectionPaper.paper_id))
    .where(CollectionPaper.collection_id == Collection.id)
    .correlate_except(CollectionPaper)
    .scalar_subquery()
)

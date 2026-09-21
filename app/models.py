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
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.schemas import JobStatus


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    query: Mapped[str] = mapped_column(Text)
    from_year: Mapped[int | None]
    to_year: Mapped[int | None]
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

    job_papers: Mapped[list["JobPaper"]] = relationship(
        back_populates="job",
        order_by="JobPaper.rank",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def results(self) -> list["Paper"]:
        return [job_paper.paper for job_paper in self.job_papers]


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, unique=True)
    doi: Mapped[str | None] = mapped_column(Text, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    publication_year: Mapped[int | None]
    cited_by_count: Mapped[int]
    authors: Mapped[list[str]] = mapped_column(ARRAY(Text))
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

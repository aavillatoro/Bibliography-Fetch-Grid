from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import Collection, CollectionPaper, Paper


class NameTaken(Exception):
    pass


class UnknownPapers(Exception):
    def __init__(self, source_ids: list[str]):
        self.source_ids = source_ids


def get_collection(session: Session, collection_id: UUID) -> Collection | None:
    return session.scalar(
        select(Collection)
        .where(Collection.id == collection_id)
        .options(selectinload(Collection.collection_papers))
        # paper_count and the paper list go stale after a write in the same session
        .execution_options(populate_existing=True)
    )


def list_collections(session: Session) -> list[Collection]:
    return list(session.scalars(select(Collection).order_by(Collection.name)))


def create_collection(session: Session, name: str) -> Collection:
    collection = Collection(name=name)
    session.add(collection)
    commit_name(session)
    return get_collection(session, collection.id)


def rename_collection(session: Session, collection: Collection, name: str) -> Collection:
    collection.name = name
    commit_name(session)
    return collection


def commit_name(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise NameTaken


def delete_collection(session: Session, collection: Collection) -> None:
    session.delete(collection)
    session.commit()


def add_papers(session: Session, collection_id: UUID, source_ids: list[str]) -> None:
    paper_ids = dict(
        session.execute(
            select(Paper.source_id, Paper.id).where(Paper.source_id.in_(source_ids))
        )
        .tuples()
        .all()
    )
    unknown = [source_id for source_id in source_ids if source_id not in paper_ids]
    if unknown:
        raise UnknownPapers(unknown)

    session.execute(
        insert(CollectionPaper)
        .values(
            [
                {"collection_id": collection_id, "paper_id": paper_id}
                for paper_id in paper_ids.values()
            ]
        )
        .on_conflict_do_nothing()
    )
    session.commit()


def remove_papers(session: Session, collection_id: UUID, source_ids: list[str]) -> None:
    session.execute(
        delete(CollectionPaper).where(
            CollectionPaper.collection_id == collection_id,
            CollectionPaper.paper_id.in_(
                select(Paper.id).where(Paper.source_id.in_(source_ids))
            ),
        )
    )
    session.commit()

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings


def make_engine(url: str) -> Engine:
    # return timestamps in utc regardless of the server timezone
    return create_engine(url, connect_args={"options": "-c timezone=utc"})


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session():
    with SessionLocal() as session:
        yield session

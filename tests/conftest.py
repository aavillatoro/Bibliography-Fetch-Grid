import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import db
from app.config import settings
from app.main import app

TABLES = "job_papers, papers, jobs"


@pytest.fixture(scope="session")
def engine():
    url = settings.test_database_url
    if not url or url == settings.database_url:
        pytest.exit("TEST_DATABASE_URL must be set to a separate database", returncode=1)

    # migrate for real so the tests also cover the migrations
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    engine = db.make_engine(url)
    db.SessionLocal.configure(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    with engine.begin() as conn:
        conn.execute(text(f"truncate {TABLES} restart identity cascade"))
    with db.SessionLocal() as session:
        yield session


@pytest.fixture
def client(session):
    with TestClient(app) as client:
        yield client

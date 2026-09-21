import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import jobs, openalex
from app.main import app
from app.models import Job, JobPaper, Paper
from app.schemas import JobRequest, JobStatus

WORK = {
    "id": "https://openalex.org/W1",
    "doi": "https://doi.org/10.1/abc",
    "title": "Paper One",
    "publication_year": 2020,
    "cited_by_count": 42,
    "authorships": [
        {"author": {"id": None, "display_name": "Ada Lovelace"}},
        {"author": {}},
    ],
}


@pytest.fixture
def fake_fetch(monkeypatch):
    class FakeFetch:
        def __init__(self):
            self.calls = []
            self.works = [WORK]

        def __call__(self, request):
            self.calls.append(request)
            return self.works

    fake = FakeFetch()
    monkeypatch.setattr(openalex, "fetch_works", fake)
    return fake


def test_create_job_returns_queued_summary(client, fake_fetch):
    response = client.post("/jobs", json={"query": "distributed systems"})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["limit"] == 25
    assert body["sort"] == "citations_desc"
    assert "results" not in body


def test_job_completes_with_normalized_results(client, fake_fetch):
    job_id = client.post(
        "/jobs",
        json={"query": "distributed systems", "from_year": 2018, "to_year": 2026, "limit": 10},
    ).json()["id"]

    body = client.get(f"/jobs/{job_id}").json()

    assert body["status"] == "COMPLETED"
    assert body["created_at"].endswith("Z")
    assert body["started_at"].endswith("Z")
    assert body["completed_at"].endswith("Z")
    assert body["papers_found"] == 1
    assert body["results"] == [
        {
            "source_id": "https://openalex.org/W1",
            "doi": "https://doi.org/10.1/abc",
            "title": "Paper One",
            "publication_year": 2020,
            "cited_by_count": 42,
            "authors": ["Ada Lovelace"],
        }
    ]
    assert fake_fetch.calls[0].from_year == 2018


def test_upstream_error_marks_job_failed(client, monkeypatch):
    def fetch(request):
        raise httpx.ConnectTimeout("timed out")

    monkeypatch.setattr(openalex, "fetch_works", fetch)

    job_id = client.post("/jobs", json={"query": "x"}).json()["id"]
    body = client.get(f"/jobs/{job_id}").json()

    assert body["status"] == "FAILED"
    assert body["error"] == "ConnectTimeout: timed out"
    assert body["results"] == []


def test_list_jobs_omits_results(client, fake_fetch):
    client.post("/jobs", json={"query": "a"})
    client.post("/jobs", json={"query": "b"})

    body = client.get("/jobs").json()

    assert [job["query"] for job in body] == ["b", "a"]
    assert all("results" not in job for job in body)


def test_unknown_job_returns_404(client):
    response = client.get("/jobs/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": " , "},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": 201},
        {"query": "x", "sort": "random"},
        {"query": "x", "from_year": 2026, "to_year": 2018},
    ],
)
def test_invalid_requests_are_rejected(client, payload):
    assert client.post("/jobs", json=payload).status_code == 422


def test_build_params_maps_sort_and_year_range():
    params = openalex.build_params(
        JobRequest(query="q", from_year=2018, to_year=2026, limit=50, sort="year_desc")
    )
    assert params == {
        "filter": "title_and_abstract.search:q,publication_year:2018-2026",
        "per-page": 50,
        "sort": "publication_year:desc",
    }


def test_build_params_strips_commas_from_query():
    params = openalex.build_params(JobRequest(query='"consensus", raft'))
    assert params["filter"] == 'title_and_abstract.search:"consensus"  raft'


@pytest.mark.parametrize(
    "from_year, to_year, expected",
    [
        (2018, None, "publication_year:>2017"),
        (None, 2026, "publication_year:<2027"),
        (None, None, None),
    ],
)
def test_open_ended_year_filters(from_year, to_year, expected):
    assert openalex.year_range_filter(from_year, to_year) == expected


def test_normalize_work_handles_missing_fields():
    paper = openalex.normalize_work({"id": "https://openalex.org/W2"})
    assert paper.doi is None
    assert paper.cited_by_count == 0
    assert paper.authors == []


def run_job(client, **payload):
    job_id = client.post("/jobs", json={"query": "q", **payload}).json()["id"]
    return client.get(f"/jobs/{job_id}").json()


def test_jobs_survive_restart(client, fake_fetch):
    job = run_job(client)

    with TestClient(app) as restarted:
        body = restarted.get(f"/jobs/{job['id']}").json()

    assert body["status"] == "COMPLETED"
    assert body["results"] == job["results"]


def test_startup_fails_jobs_left_running(session, fake_fetch):
    for status in (JobStatus.QUEUED, JobStatus.FETCHING, JobStatus.COMPLETED):
        session.add(Job(query="q", limit=25, sort="relevance", status=status))
    session.commit()

    with TestClient(app):
        pass

    statuses = session.scalars(select(Job.status).order_by(Job.status)).all()
    assert statuses == [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.FAILED]
    errors = session.scalars(select(Job.error).where(Job.status == JobStatus.FAILED)).all()
    assert errors == ["interrupted by server restart"] * 2


def test_duplicate_works_in_one_response_are_stored_once(client, fake_fetch):
    other = {**WORK, "id": "https://openalex.org/W2", "title": "Paper Two"}
    fake_fetch.works = [WORK, other, WORK]

    body = run_job(client)

    assert body["papers_found"] == 2
    assert [paper["source_id"] for paper in body["results"]] == [WORK["id"], other["id"]]


def test_jobs_share_papers_and_refresh_citations(client, session, fake_fetch):
    first = run_job(client)
    fake_fetch.works = [{**WORK, "cited_by_count": 50}]
    second = run_job(client)

    assert session.scalar(select(func.count()).select_from(Paper)) == 1
    assert session.scalar(select(func.count()).select_from(JobPaper)) == 2
    assert client.get(f"/jobs/{first['id']}").json()["results"][0]["cited_by_count"] == 50
    assert second["results"][0]["cited_by_count"] == 50


def test_rerunning_a_job_replaces_its_results(client, session, fake_fetch):
    job = run_job(client)

    jobs.process_job(job["id"], JobRequest(query="q"))

    assert session.scalar(select(func.count()).select_from(JobPaper)) == 1
    assert client.get(f"/jobs/{job['id']}").json()["papers_found"] == 1


def test_failed_job_keeps_no_partial_results(client, session, monkeypatch):
    def broken_normalize(work):
        raise KeyError("id")

    monkeypatch.setattr(openalex, "fetch_works", lambda request: [WORK])
    monkeypatch.setattr(openalex, "normalize_work", broken_normalize)

    body = run_job(client)

    assert body["status"] == "FAILED"
    assert body["error"] == "KeyError: 'id'"
    assert session.scalar(select(func.count()).select_from(JobPaper)) == 0


def test_list_jobs_filters_and_paginates(client, fake_fetch, monkeypatch):
    run_job(client, query="a")
    run_job(client, query="b")
    monkeypatch.setattr(openalex, "fetch_works", lambda request: 1 / 0)
    run_job(client, query="c")

    assert [job["query"] for job in client.get("/jobs?status=FAILED").json()] == ["c"]
    assert [job["query"] for job in client.get("/jobs?limit=1&offset=1").json()] == ["b"]
    assert client.get("/jobs?limit=0").status_code == 422


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}

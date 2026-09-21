import httpx
import pytest
from fastapi.testclient import TestClient

from app import jobs, openalex
from app.main import app
from app.schemas import JobRequest

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
def client():
    jobs._jobs.clear()
    return TestClient(app)


@pytest.fixture
def fake_fetch(monkeypatch):
    calls = []

    def fetch(request):
        calls.append(request)
        return [WORK]

    monkeypatch.setattr(openalex, "fetch_works", fetch)
    return calls


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
    assert body["started_at"] is not None
    assert body["completed_at"] is not None
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
    assert fake_fetch[0].from_year == 2018


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

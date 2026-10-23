import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import jobs, openalex
from app.config import settings
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
    "type": "article",
    "primary_location": {"source": {"display_name": "Journal of Things"}},
    "abstract_inverted_index": {"We": [0], "study": [1, 3], "things": [2]},
    "open_access": {"oa_url": "https://example.org/w1.pdf"},
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
    assert body["sort"] == "relevance"
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
            "work_type": "article",
            "venue": "Journal of Things",
            "abstract": "We study things study",
            "open_access_url": "https://example.org/w1.pdf",
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
        {"query": "x", "limit": 501},
        {"query": "x", "sort": "random"},
        {"query": "x", "min_citations": -1},
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
        "per_page": 50,
        "sort": "publication_year:desc",
    }


def test_build_params_filters_by_min_citations():
    params = openalex.build_params(JobRequest(query="q", min_citations=50))
    assert params["filter"] == "title_and_abstract.search:q,cited_by_count:>49"
    assert params["sort"] == "relevance_score:desc"

    unfiltered = openalex.build_params(JobRequest(query="q", min_citations=0))
    assert "cited_by_count" not in unfiltered["filter"]


def test_build_params_caps_per_page():
    assert openalex.build_params(JobRequest(query="q", limit=500))["per_page"] == 100


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
    assert paper.venue is None
    assert paper.abstract is None
    assert paper.open_access_url is None


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


def test_export_downloads_completed_job(client, fake_fetch):
    job = run_job(client, query='"distributed systems"')

    response = client.get(f"/jobs/{job['id']}/export?format=ris")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-research-info-systems")
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="distributed-systems.ris"'
    )
    assert "TI  - Paper One\n" in response.text


def test_export_rejects_unfinished_and_unknown_jobs(client, session):
    job = Job(query="q", limit=25, sort="relevance", status=JobStatus.FETCHING)
    session.add(job)
    session.commit()

    assert client.get(f"/jobs/{job.id}/export").status_code == 409
    assert client.get(f"/jobs/{job.id}/export?format=docx").status_code == 422
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/jobs/{missing}/export").status_code == 404


def test_index_serves_ui(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert client.get("/static/app.js").status_code == 200


@pytest.fixture
def fake_openalex(monkeypatch):
    class FakeOpenAlex:
        def __init__(self):
            self.responses = []
            self.calls = []
            self.sleeps = []

        def get(self, url, params=None, timeout=None):
            self.calls.append(params)
            status, headers, *results = self.responses.pop(0)
            body = {"results": results[0] if results else [WORK]} if status == 200 else {}
            return httpx.Response(
                status, headers=headers, json=body, request=httpx.Request("GET", url)
            )

    fake = FakeOpenAlex()
    monkeypatch.setattr(openalex.httpx, "get", fake.get)
    monkeypatch.setattr(openalex, "sleep", fake.sleeps.append)
    monkeypatch.setattr(settings, "openalex_mailto", None)
    monkeypatch.setattr(settings, "openalex_api_key", None)
    return fake


def test_fetch_works_retries_with_backoff_and_retry_after(fake_openalex):
    fake_openalex.responses = [(503, {}), (503, {"Retry-After": "7"}), (200, {})]

    assert openalex.fetch_works(JobRequest(query="q")) == [WORK]
    assert len(fake_openalex.calls) == 3
    assert fake_openalex.sleeps == [1.0, 7.0]


def test_fetch_works_gives_up_after_max_attempts(client, fake_openalex):
    fake_openalex.responses = [(500, {}), (502, {}), (503, {})]

    body = run_job(client)

    assert body["status"] == "FAILED"
    assert body["error"] == "OpenAlexError: openalex returned 503 after 3 attempts, try again later"
    assert fake_openalex.sleeps == [1.0, 2.0]


def test_rate_limit_fails_job_without_retrying(client, fake_openalex):
    fake_openalex.responses = [(429, {"Retry-After": "60"})]

    body = run_job(client)

    assert body["status"] == "FAILED"
    assert body["error"] == (
        "OpenAlexError: openalex daily budget or rate limit hit (429), "
        "try again later or set OPENALEX_API_KEY for a larger budget"
    )
    assert len(fake_openalex.calls) == 1
    assert fake_openalex.sleeps == []


def test_rate_limit_message_skips_key_hint_when_key_set(fake_openalex, monkeypatch):
    monkeypatch.setattr(settings, "openalex_api_key", "secret-key")
    fake_openalex.responses = [(429, {})]

    with pytest.raises(openalex.OpenAlexError) as error:
        openalex.fetch_works(JobRequest(query="q"))
    assert str(error.value) == "openalex daily budget or rate limit hit (429), try again later"


def works(start, stop):
    return [{**WORK, "id": f"https://openalex.org/W{i}"} for i in range(start, stop)]


def test_fetch_works_paginates_in_rank_order_and_dedupes(fake_openalex):
    # page 2 repeats W100, as happens when rankings shift between requests
    fake_openalex.responses = [
        (200, {}, works(1, 101)),
        (200, {}, works(100, 200)),
        (200, {}, works(200, 261)),
    ]

    result = openalex.fetch_works(JobRequest(query="q", limit=250))

    assert [work["id"] for work in result] == [w["id"] for w in works(1, 251)]
    assert [call["page"] for call in fake_openalex.calls] == [1, 2, 3]
    assert all(call["per_page"] == 100 for call in fake_openalex.calls)
    assert all("cursor" not in call for call in fake_openalex.calls)


def test_fetch_works_stops_when_results_run_out(client, fake_openalex):
    fake_openalex.responses = [(200, {}, works(1, 101)), (200, {}, works(101, 131))]

    body = run_job(client, limit=500)

    assert body["status"] == "COMPLETED"
    assert body["papers_found"] == 130
    assert [call["page"] for call in fake_openalex.calls] == [1, 2]


def test_fetch_works_does_not_retry_client_errors(fake_openalex, monkeypatch):
    monkeypatch.setattr(settings, "openalex_api_key", "secret-key")
    fake_openalex.responses = [(403, {})]

    with pytest.raises(openalex.OpenAlexError) as error:
        openalex.fetch_works(JobRequest(query="q"))
    assert str(error.value) == "openalex returned 403"
    assert fake_openalex.sleeps == []


def test_fetch_works_sends_mailto_when_configured(fake_openalex, monkeypatch):
    fake_openalex.responses = [(200, {}), (200, {})]

    openalex.fetch_works(JobRequest(query="q"))
    monkeypatch.setattr(settings, "openalex_mailto", "me@example.org")
    openalex.fetch_works(JobRequest(query="q"))

    assert "mailto" not in fake_openalex.calls[0]
    assert fake_openalex.calls[1]["mailto"] == "me@example.org"


def test_fetch_works_sends_api_key_when_configured(fake_openalex, monkeypatch):
    fake_openalex.responses = [(200, {}), (200, {})]

    openalex.fetch_works(JobRequest(query="q"))
    monkeypatch.setattr(settings, "openalex_api_key", "secret-key")
    openalex.fetch_works(JobRequest(query="q"))

    assert "api_key" not in fake_openalex.calls[0]
    assert fake_openalex.calls[1]["api_key"] == "secret-key"


@pytest.mark.parametrize(
    "value, expected",
    [
        ("3", 3.0),
        ("-5", 0.0),
        ("Wed, 21 Oct 2015 07:28:00 GMT", 0.0),
        ("soon", None),
        (None, None),
    ],
)
def test_parse_retry_after(value, expected):
    assert openalex.parse_retry_after(value) == expected


def test_retry_delay_is_capped():
    response = httpx.Response(429, headers={"Retry-After": "3600"})
    assert openalex.retry_delay(response, 1) == openalex.MAX_DELAY

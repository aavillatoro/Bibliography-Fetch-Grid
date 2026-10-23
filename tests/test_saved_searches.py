import threading
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text, update

from app import db, jobs, openalex, saved_searches, worker
from app.models import Job, SavedSearch

MISSING = "00000000-0000-0000-0000-000000000000"


def work(n, **fields):
    return {"id": f"https://openalex.org/W{n}", "title": f"Paper {n}", **fields}


@pytest.fixture
def results(monkeypatch):
    class Results:
        works = []

    def fetch(request):
        if isinstance(Results.works, Exception):
            raise Results.works
        return Results.works

    monkeypatch.setattr(openalex, "fetch_works", fetch)
    return Results


def drain():
    while worker.run_once():
        pass


def create(client, **fields):
    response = client.post(
        "/saved-searches", json={"name": "Raft", "query": "raft consensus", **fields}
    )
    assert response.status_code == 201
    return response.json()


def run(client, results, search_id, works):
    results.works = works
    job = client.post(f"/saved-searches/{search_id}/run").json()
    drain()
    return client.get(f"/jobs/{job['id']}").json()


def diff(client, search_id):
    return client.get(f"/saved-searches/{search_id}/diff").json()


def source_ids(papers):
    return [paper["source_id"] for paper in papers]


def job_count(session):
    return session.scalar(select(func.count()).select_from(Job))


def set_next_run(session, search_id, when):
    session.execute(
        update(SavedSearch).where(SavedSearch.id == search_id).values(next_run_at=when)
    )
    session.commit()


def test_create_list_get_and_delete(client, session, results):
    search = create(client, from_year=2018, limit=50, sort="year_desc")
    assert search["schedule"] is None
    assert search["next_run_at"] is None
    assert search["latest_job"] is None
    assert search["new_since_last_run"] == 0
    assert (search["from_year"], search["limit"], search["sort"]) == (2018, 50, "year_desc")

    job = run(client, results, search["id"], [work(1)])

    assert [s["name"] for s in client.get("/saved-searches").json()] == ["Raft"]
    detail = client.get(f"/saved-searches/{search['id']}").json()
    assert detail["latest_job"]["id"] == job["id"]
    assert [run["id"] for run in detail["runs"]] == [job["id"]]

    assert client.delete(f"/saved-searches/{search['id']}").status_code == 204
    assert client.get(f"/saved-searches/{search['id']}").status_code == 404
    # past runs stay in the job history
    kept = client.get(f"/jobs/{job['id']}").json()
    assert kept["status"] == "COMPLETED"
    assert kept["saved_search_id"] is None


def test_invalid_saved_searches_are_rejected(client):
    create(client)

    assert client.post("/saved-searches", json={"name": "Raft", "query": "x"}).status_code == 409
    for payload in [
        {"name": "A", "query": "x", "schedule": "hourly"},
        {"name": " ", "query": "x"},
        {"name": "A", "query": "x", "limit": 501},
        {"name": "A", "query": "x", "from_year": 2026, "to_year": 2018},
    ]:
        assert client.post("/saved-searches", json=payload).status_code == 422


def test_unknown_saved_search_returns_404(client):
    for method, path in [
        ("get", ""),
        ("delete", ""),
        ("post", "/run"),
        ("get", "/diff"),
    ]:
        response = getattr(client, method)(f"/saved-searches/{MISSING}{path}")
        assert response.status_code == 404


def test_run_enqueues_through_the_job_queue(client, results):
    search = create(client, min_citations=10, limit=40)

    response = client.post(f"/saved-searches/{search['id']}/run")

    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "QUEUED"
    assert job["saved_search_id"] == search["id"]
    assert (job["query"], job["min_citations"], job["limit"]) == ("raft consensus", 10, 40)

    results.works = [work(1)]
    drain()
    assert client.get(f"/jobs/{job['id']}").json()["status"] == "COMPLETED"


def test_diff_returns_papers_new_in_latest_run(client, results):
    search = create(client)
    first = run(client, results, search["id"], [work(1), work(2)])
    second = run(client, results, search["id"], [work(3), work(2), work(4)])

    body = diff(client, search["id"])

    assert body["latest_job_id"] == second["id"]
    assert body["previous_job_id"] == first["id"]
    assert source_ids(body["papers"]) == [work(3)["id"], work(4)["id"]]
    assert client.get("/saved-searches").json()[0]["new_since_last_run"] == 2


def test_diff_matches_papers_by_openalex_id(client, results):
    search = create(client)
    run(client, results, search["id"], [work(1)])
    run(client, results, search["id"], [work(1, title="Retitled"), work(2)])

    assert source_ids(diff(client, search["id"])["papers"]) == [work(2)["id"]]


def test_diff_needs_two_completed_runs(client, results):
    search = create(client)
    assert diff(client, search["id"]) == {
        "latest_job_id": None,
        "previous_job_id": None,
        "papers": [],
    }

    only = run(client, results, search["id"], [work(1)])

    assert diff(client, search["id"]) == {
        "latest_job_id": only["id"],
        "previous_job_id": None,
        "papers": [],
    }


def test_diff_skips_failed_runs(client, results):
    search = create(client)
    first = run(client, results, search["id"], [work(1)])
    second = run(client, results, search["id"], [work(1), work(2)])
    failed = run(client, results, search["id"], RuntimeError("upstream down"))
    assert failed["status"] == "FAILED"

    body = diff(client, search["id"])

    assert (body["latest_job_id"], body["previous_job_id"]) == (second["id"], first["id"])
    assert source_ids(body["papers"]) == [work(2)["id"]]
    assert client.get(f"/saved-searches/{search['id']}").json()["latest_job"]["id"] == failed["id"]


def test_new_scheduled_search_runs_once_then_waits(client, session, results):
    results.works = [work(1)]
    search = create(client, schedule="daily")
    assert search["next_run_at"] is not None

    drain()

    runs = session.scalars(select(Job).where(Job.saved_search_id == search["id"])).all()
    assert [run.status for run in runs] == ["COMPLETED"]
    next_run_at = session.get(SavedSearch, search["id"]).next_run_at
    assert next_run_at - jobs.now() > timedelta(hours=23)

    drain()
    assert job_count(session) == 1


def test_only_due_scheduled_searches_are_enqueued(client, session):
    unscheduled = create(client, name="Never")
    later = create(client, name="Later", schedule="weekly")
    due = create(client, name="Due", schedule="weekly")
    set_next_run(session, later["id"], jobs.now() + timedelta(hours=1))
    assert unscheduled["next_run_at"] is None

    with db.SessionLocal() as worker_session:
        assert saved_searches.enqueue_due_searches(worker_session) == 1
        worker_session.commit()

    assert [str(id) for id in session.scalars(select(Job.saved_search_id))] == [due["id"]]


def test_missed_runs_collapse_into_one(client, session):
    search = create(client, schedule="daily")
    set_next_run(session, search["id"], jobs.now() - timedelta(days=7))

    with db.SessionLocal() as worker_session:
        assert saved_searches.enqueue_due_searches(worker_session) == 1
        worker_session.commit()
        assert saved_searches.enqueue_due_searches(worker_session) == 0

    session.expire_all()
    next_run_at = session.get(SavedSearch, search["id"]).next_run_at
    assert timedelta(hours=23) < next_run_at - jobs.now() <= timedelta(days=1)


def test_two_workers_checking_at_once_enqueue_once(client, session):
    create(client, schedule="daily")

    with db.SessionLocal() as first, db.SessionLocal() as second:
        # without skip locked, second would wait on first forever in this one thread
        second.execute(text("set local lock_timeout = '2s'"))
        # first holds the row lock until it commits
        assert saved_searches.enqueue_due_searches(first) == 1
        assert saved_searches.enqueue_due_searches(second) == 0
        second.commit()
        first.commit()

    with db.SessionLocal() as late:
        assert saved_searches.enqueue_due_searches(late) == 0

    assert job_count(session) == 1


def test_racing_workers_enqueue_once(client, session):
    for n in range(5):
        create(client, name=f"Search {n}", schedule="daily")
    start = threading.Barrier(4)
    errors = []

    def check():
        try:
            with db.SessionLocal() as worker_session:
                start.wait()
                saved_searches.enqueue_due_searches(worker_session)
                worker_session.commit()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=check) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert job_count(session) == 5

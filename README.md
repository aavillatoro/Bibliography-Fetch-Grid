# Bibliography Fetch Grid

A literature search tool built on scholarly metadata from [OpenAlex](https://openalex.org). Search a topic, get a ranked, deduplicated table of papers, save the ones you want into collections, and export them as BibTeX, RIS or CSV for a reference manager.

Each search runs as a background job: the API returns a job ID immediately and the web UI polls until the results are stored in PostgreSQL.

## Setup

Requires Python 3.13 and PostgreSQL 17.

```sh
python -m venv venv
venv\Scripts\activate
pip install -r requirements-dev.txt
```

Create a role and two databases (the test suite truncates its database):

```sql
create role bfg login password 'change-me';
create database bibliography_fetch_grid owner bfg;
create database bibliography_fetch_grid_test owner bfg;
```

Copy `.env.example` to `.env`, set the password, then apply migrations and start the API:

```sh
alembic upgrade head
uvicorn app.main:app --reload
```

The web UI is at http://localhost:8000 and the interactive API docs are at http://localhost:8000/docs.

## API

| Method | Path | |
|---|---|---|
| `POST` | `/jobs` | Create a job. Returns `202` with status `QUEUED`. |
| `GET` | `/jobs` | List jobs, newest first. Query params: `status`, `limit` (max 200), `offset`. |
| `GET` | `/jobs/{id}` | Job status, timestamps and ranked results. |
| `GET` | `/jobs/{id}/export` | Download a completed job's results. `format` is `bibtex` (default), `ris` or `csv`. Returns `409` until the job completes. |
| `POST` | `/collections` | Create a collection from `{"name": "..."}`. Names are unique; a duplicate returns `409`. |
| `GET` | `/collections` | List collections by name, each with its `paper_count`. |
| `GET` | `/collections/{id}` | A collection and its papers, in the order they were added. |
| `PATCH` | `/collections/{id}` | Rename a collection. |
| `DELETE` | `/collections/{id}` | Delete a collection. Its papers stay in the database. |
| `POST` | `/collections/{id}/papers` | Add papers from `{"source_ids": [...]}`, the OpenAlex IDs of papers a job has already fetched. |
| `DELETE` | `/collections/{id}/papers` | Remove papers. Repeat the `source_id` query param for each one. |
| `GET` | `/collections/{id}/export` | Download a collection. Same `format` values as the job export. |
| `POST` | `/saved-searches` | Save a job request with a unique `name` and an optional `schedule` of `daily` or `weekly`. |
| `GET` | `/saved-searches` | List saved searches by name, each with its latest run and `new_since_last_run` count. |
| `GET` | `/saved-searches/{id}` | A saved search and its 20 most recent runs. |
| `DELETE` | `/saved-searches/{id}` | Delete a saved search. Its past runs stay in the job list. |
| `POST` | `/saved-searches/{id}/run` | Queue a run now. Returns `202` with the new job. |
| `GET` | `/saved-searches/{id}/diff` | Papers in the latest completed run that were not in the previous completed run, by OpenAlex ID, in rank order. |
| `GET` | `/health` | Checks the database connection. |

```json
POST /jobs
{
  "query": "\"distributed systems\"",
  "from_year": 2018,
  "to_year": 2026,
  "min_citations": 50,
  "limit": 200,
  "sort": "relevance"
}
```

`query` is matched against titles and abstracts; double quotes force a phrase match. `sort` is `relevance` (default), `citations_desc` or `year_desc`. `min_citations` drops papers cited fewer times than that, so it combines with any sort. `limit` is capped at 500. OpenAlex returns at most 100 works per request, so larger limits fetch several pages, in rank order with duplicates dropped.

Jobs move through `QUEUED → FETCHING → PROCESSING → COMPLETED`, or to `FAILED` with an `error` message. Each paper carries its title, authors, year, venue, type, citation count, DOI, abstract and open-access link. Papers are deduplicated by OpenAlex ID and shared across jobs.

Jobs are queued in Postgres and run one at a time by a worker thread inside the API process, so queued jobs survive a restart. A running job updates a heartbeat every 10 seconds. If the process dies mid-job, the job is requeued once its heartbeat is 60 seconds old and runs again, up to 3 attempts, then fails. A rerun spends that job's OpenAlex calls again. Errors raised by the job itself, such as an OpenAlex failure, fail it without a retry. Running several API processes is safe: each claims jobs with `SELECT … FOR UPDATE SKIP LOCKED`. On shutdown the worker stops taking jobs and waits up to 30 seconds for the current one.

A scheduled saved search runs as soon as it is created, then once per day or week, counted from its last run; runs missed while the server was down collapse into one. The worker enqueues due searches with `SELECT … FOR UPDATE SKIP LOCKED`, so two workers checking at once enqueue each search once. Each scheduled run costs the same OpenAlex calls as a manual one.

OpenAlex bills per request: $1 per 1,000 search calls, against a daily budget of $0.10 without a key and $1 with a free API key. A job costs one call per 100 papers, so a 500-paper job is 5 calls. Set `OPENALEX_API_KEY` to raise the daily budget. `OPENALEX_MAILTO` is an optional contact email sent to OpenAlex with each request.

A 429 means the daily budget or rate limit was hit; the job fails right away with that message instead of retrying. 5xx responses are retried up to 3 attempts, waiting for `Retry-After` when sent and backing off exponentially otherwise.

## Tests

```sh
pytest
```

Tests run against `TEST_DATABASE_URL`, apply the migrations from scratch, and mock OpenAlex.

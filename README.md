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

Jobs move through `QUEUED → FETCHING → PROCESSING → COMPLETED`, or to `FAILED` with an `error` message. Each paper carries its title, authors, year, venue, type, citation count, DOI, abstract and open-access link. Papers are deduplicated by OpenAlex ID and shared across jobs. A job still running when the API restarts is marked `FAILED`.

OpenAlex bills per request: $1 per 1,000 search calls, against a daily budget of $0.10 without a key and $1 with a free API key. A job costs one call per 100 papers, so a 500-paper job is 5 calls. Set `OPENALEX_API_KEY` to raise the daily budget. `OPENALEX_MAILTO` is an optional contact email sent to OpenAlex with each request.

A 429 means the daily budget or rate limit was hit; the job fails right away with that message instead of retrying. 5xx responses are retried up to 3 attempts, waiting for `Retry-After` when sent and backing off exponentially otherwise.

## Tests

```sh
pytest
```

Tests run against `TEST_DATABASE_URL`, apply the migrations from scratch, and mock OpenAlex.

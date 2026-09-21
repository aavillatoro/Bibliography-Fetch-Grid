# Bibliography Fetch Grid

Asynchronous processing jobs over scholarly metadata from [OpenAlex](https://openalex.org). Submit a query, get a job ID back immediately, and poll for normalized, ranked results stored in PostgreSQL.

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

Interactive docs are at http://localhost:8000/docs.

## API

| Method | Path | |
|---|---|---|
| `POST` | `/jobs` | Create a job. Returns `202` with status `QUEUED`. |
| `GET` | `/jobs` | List jobs, newest first. Query params: `status`, `limit` (max 200), `offset`. |
| `GET` | `/jobs/{id}` | Job status, timestamps and ranked results. |
| `GET` | `/health` | Checks the database connection. |

```json
POST /jobs
{
  "query": "\"distributed systems\"",
  "from_year": 2018,
  "to_year": 2026,
  "limit": 200,
  "sort": "citations_desc"
}
```

`query` is matched against titles and abstracts; double quotes force a phrase match. `sort` is `citations_desc` (default), `year_desc` or `relevance`. `limit` is capped at 200, one OpenAlex page.

Jobs move through `QUEUED → FETCHING → PROCESSING → COMPLETED`, or to `FAILED` with an `error` message. Papers are deduplicated by OpenAlex ID and shared across jobs. A job still running when the API restarts is marked `FAILED`.

## Tests

```sh
pytest
```

Tests run against `TEST_DATABASE_URL`, apply the migrations from scratch, and mock OpenAlex.

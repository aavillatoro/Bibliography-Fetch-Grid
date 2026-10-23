from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from time import sleep

import httpx

from app.config import settings
from app.schemas import JobRequest, Paper

BASE_URL = "https://api.openalex.org/works"
MAX_PER_PAGE = 100

MAX_ATTEMPTS = 3
BASE_DELAY = 1.0
# a huge retry-after would otherwise stall the job worker
MAX_DELAY = 30.0

SORT_PARAMS = {
    "citations_desc": "cited_by_count:desc",
    "year_desc": "publication_year:desc",
    "relevance": "relevance_score:desc",
}


def build_params(request: JobRequest) -> dict:
    # the plain `search` param matches full text, which ranks off-topic
    # highly cited papers first; commas would split the filter string
    filters = [f"title_and_abstract.search:{request.query.replace(',', ' ')}"]

    year_filter = year_range_filter(request.from_year, request.to_year)
    if year_filter:
        filters.append(year_filter)

    if request.min_citations:
        filters.append(f"cited_by_count:>{request.min_citations - 1}")

    return {
        "filter": ",".join(filters),
        "per_page": min(request.limit, MAX_PER_PAGE),
        "sort": SORT_PARAMS[request.sort],
    }


def year_range_filter(from_year: int | None, to_year: int | None) -> str | None:
    if from_year is not None and to_year is not None:
        return f"publication_year:{from_year}-{to_year}"
    if from_year is not None:
        return f"publication_year:>{from_year - 1}"
    if to_year is not None:
        return f"publication_year:<{to_year + 1}"
    return None


def normalize_work(work: dict) -> Paper:
    authors = [
        authorship["author"]["display_name"]
        for authorship in work.get("authorships") or []
        if authorship.get("author", {}).get("display_name")
    ]

    source = (work.get("primary_location") or {}).get("source") or {}

    return Paper(
        source_id=work["id"],
        doi=work.get("doi"),
        title=work.get("title"),
        publication_year=work.get("publication_year"),
        cited_by_count=work.get("cited_by_count") or 0,
        authors=authors,
        work_type=work.get("type"),
        venue=source.get("display_name"),
        abstract=rebuild_abstract(work.get("abstract_inverted_index")),
        open_access_url=(work.get("open_access") or {}).get("oa_url"),
    )


def rebuild_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    # openalex ships abstracts as word -> positions for licensing reasons
    if not inverted_index:
        return None
    positions = {
        position: word
        for word, word_positions in inverted_index.items()
        for position in word_positions
    }
    return " ".join(positions[i] for i in sorted(positions))


class OpenAlexError(Exception):
    pass


def fetch_works(request: JobRequest) -> list[dict]:
    params = build_params(request)
    if settings.openalex_mailto:
        params["mailto"] = settings.openalex_mailto
    if settings.openalex_api_key:
        params["api_key"] = settings.openalex_api_key

    works = []
    seen = set()
    page = 1
    while len(works) < request.limit:
        results = fetch_page({**params, "page": page})
        # rankings can shift between page requests, repeating a work
        for work in results:
            if work["id"] not in seen:
                seen.add(work["id"])
                works.append(work)
        if len(results) < params["per_page"]:
            break
        page += 1
    return works[: request.limit]


def fetch_page(params: dict) -> list[dict]:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = httpx.get(BASE_URL, params=params, timeout=10)
        if response.is_success:
            return response.json()["results"]
        if response.status_code == 429:
            # billing is per request, so retrying a spent budget only adds calls
            raise OpenAlexError(rate_limit_message())
        if response.status_code < 500:
            # httpx's own error includes the url, which would leak api_key into job.error
            raise OpenAlexError(f"openalex returned {response.status_code}")
        if attempt < MAX_ATTEMPTS:
            sleep(retry_delay(response, attempt))

    raise OpenAlexError(
        f"openalex returned {response.status_code} after {MAX_ATTEMPTS} attempts, try again later"
    )


def rate_limit_message() -> str:
    message = "openalex daily budget or rate limit hit (429), try again later"
    if not settings.openalex_api_key:
        message += " or set OPENALEX_API_KEY for a larger budget"
    return message


def retry_delay(response: httpx.Response, attempt: int) -> float:
    delay = parse_retry_after(response.headers.get("retry-after"))
    if delay is None:
        delay = BASE_DELAY * 2 ** (attempt - 1)
    return min(delay, MAX_DELAY)


def parse_retry_after(value: str | None) -> float | None:
    # the header is either delay-seconds or an http date
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max((retry_at - datetime.now(timezone.utc)).total_seconds(), 0.0)

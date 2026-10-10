import httpx

from app.schemas import JobRequest, Paper

BASE_URL = "https://api.openalex.org/works"

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

    return {
        "filter": ",".join(filters),
        "per-page": request.limit,
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


def fetch_works(request: JobRequest) -> list[dict]:
    response = httpx.get(BASE_URL, params=build_params(request), timeout=10)
    response.raise_for_status()
    return response.json()["results"]

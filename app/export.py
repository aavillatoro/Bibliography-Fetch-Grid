import csv
import io
import re
import unicodedata

from app.schemas import ExportFormat, Paper

DOI_PREFIX = "https://doi.org/"

BIBTEX_TYPES = {
    "article": "article",
    "review": "article",
    "book": "book",
    "book-chapter": "incollection",
    "dissertation": "phdthesis",
}
BIBTEX_VENUE_FIELDS = {
    "article": "journal",
    "incollection": "booktitle",
    "misc": "howpublished",
}

RIS_TYPES = {
    "article": "JOUR",
    "review": "JOUR",
    "book": "BOOK",
    "book-chapter": "CHAP",
    "dissertation": "THES",
    "dataset": "DATA",
}

CSV_COLUMNS = [
    "rank",
    "title",
    "authors",
    "year",
    "venue",
    "type",
    "citations",
    "doi",
    "open_access_url",
    "openalex_id",
    "abstract",
]

MEDIA_TYPES = {
    "bibtex": ("application/x-bibtex", "bib"),
    "ris": ("application/x-research-info-systems", "ris"),
    "csv": ("text/csv", "csv"),
}


def render(format: ExportFormat, papers: list[Paper]) -> str:
    return {"bibtex": to_bibtex, "ris": to_ris, "csv": to_csv}[format](papers)


def filename(query: str, format: ExportFormat) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_fold(query).lower()).strip("-")[:60]
    return f"{slug or 'results'}.{MEDIA_TYPES[format][1]}"


def ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def bare_doi(doi: str | None) -> str | None:
    return doi.removeprefix(DOI_PREFIX) if doi else None


def one_line(text: str) -> str:
    return " ".join(text.split())


def citation_key(paper: Paper) -> str:
    surname = paper.authors[0].split()[-1] if paper.authors else "anon"
    words = re.findall(r"[a-z]{4,}", ascii_fold(paper.title or "").lower())
    key = ascii_fold(surname).lower() + str(paper.publication_year or "")
    return re.sub(r"[^a-z0-9]", "", key + (words[0] if words else ""))


def bibtex_value(text: str) -> str:
    # braces and backslashes would unbalance or inject latex, so drop them
    text = re.sub(r"[{}\\]", "", one_line(text))
    return re.sub(r"([&%$#_])", r"\\\1", text)


def to_bibtex(papers: list[Paper]) -> str:
    used_keys: dict[str, int] = {}
    entries = []
    for paper in papers:
        base_key = citation_key(paper)
        count = used_keys.get(base_key, 0)
        used_keys[base_key] = count + 1
        # smith2020a, smith2020b, ... after the first collision
        key = base_key if count == 0 else f"{base_key}{chr(ord('a') + count - 1)}"

        entry_type = BIBTEX_TYPES.get(paper.work_type, "misc")
        fields = {
            # double braces keep acronyms capitalized
            "title": f"{{{bibtex_value(paper.title)}}}" if paper.title else None,
            "author": " and ".join(bibtex_value(a) for a in paper.authors) or None,
            "year": paper.publication_year,
            BIBTEX_VENUE_FIELDS.get(entry_type, "publisher"): (
                bibtex_value(paper.venue) if paper.venue else None
            ),
            "doi": bare_doi(paper.doi),
            "url": paper.open_access_url,
            "abstract": bibtex_value(paper.abstract) if paper.abstract else None,
        }
        lines = [f"  {name} = {{{value}}}" for name, value in fields.items() if value]
        entries.append(f"@{entry_type}{{{key},\n" + ",\n".join(lines) + "\n}\n")
    return "\n".join(entries)


def to_ris(papers: list[Paper]) -> str:
    records = []
    for paper in papers:
        lines = [("TY", RIS_TYPES.get(paper.work_type, "GEN"))]
        if paper.title:
            lines.append(("TI", one_line(paper.title)))
        lines += [("AU", author) for author in paper.authors]
        if paper.publication_year:
            lines.append(("PY", str(paper.publication_year)))
        if paper.venue:
            lines.append(("T2", one_line(paper.venue)))
        if paper.doi:
            lines.append(("DO", bare_doi(paper.doi)))
        if paper.open_access_url:
            lines.append(("UR", paper.open_access_url))
        if paper.abstract:
            lines.append(("AB", one_line(paper.abstract)))
        lines.append(("ER", ""))
        records.append("".join(f"{tag}  - {value}\n" for tag, value in lines))
    return "\n".join(records)


def to_csv(papers: list[Paper]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for rank, paper in enumerate(papers, start=1):
        writer.writerow(
            [
                rank,
                paper.title,
                "; ".join(paper.authors),
                paper.publication_year,
                paper.venue,
                paper.work_type,
                paper.cited_by_count,
                paper.doi,
                paper.open_access_url,
                paper.source_id,
                paper.abstract,
            ]
        )
    # the bom makes excel detect utf-8 instead of mangling accented names
    return "﻿" + out.getvalue()

from app import export, openalex
from app.schemas import Paper


def paper(**fields) -> Paper:
    defaults = {
        "source_id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.1/abc",
        "title": "Raft: Understandable Consensus",
        "publication_year": 2014,
        "cited_by_count": 10,
        "authors": ["Diego Ongaro", "John Ousterhout"],
        "work_type": "article",
        "venue": "USENIX ATC",
    }
    return Paper(**{**defaults, **fields})


def test_bibtex_entry():
    assert export.to_bibtex([paper()]) == (
        "@article{ongaro2014raft,\n"
        "  title = {{Raft: Understandable Consensus}},\n"
        "  author = {Diego Ongaro and John Ousterhout},\n"
        "  year = {2014},\n"
        "  journal = {USENIX ATC},\n"
        "  doi = {10.1/abc}\n"
        "}\n"
    )


def test_bibtex_keys_are_unique():
    bib = export.to_bibtex([paper(), paper(), paper()])
    assert "{ongaro2014raft," in bib
    assert "{ongaro2014rafta," in bib
    assert "{ongaro2014raftb," in bib


def test_bibtex_escapes_special_characters_and_folds_key():
    bib = export.to_bibtex(
        [paper(title="Cost & {Speed} 50%", authors=["José Núñez"], work_type="book-chapter")]
    )
    assert bib.startswith("@incollection{nunez2014cost,")
    assert r"title = {{Cost \& Speed 50\%}}" in bib
    assert "booktitle = {USENIX ATC}" in bib


def test_unknown_type_falls_back():
    minimal = paper(work_type="paratext", authors=[], title=None, doi=None, venue=None)
    assert export.to_bibtex([minimal]).startswith("@misc{anon2014,")
    assert export.to_ris([minimal]) == "TY  - GEN\nPY  - 2014\nER  - \n"


def test_ris_record():
    ris = export.to_ris([paper(abstract="Line one\nline two")])
    assert ris == (
        "TY  - JOUR\n"
        "TI  - Raft: Understandable Consensus\n"
        "AU  - Diego Ongaro\n"
        "AU  - John Ousterhout\n"
        "PY  - 2014\n"
        "T2  - USENIX ATC\n"
        "DO  - 10.1/abc\n"
        "AB  - Line one line two\n"
        "ER  - \n"
    )


def test_csv_has_bom_header_and_rank():
    lines = export.to_csv([paper(), paper(title="Second")]).splitlines()
    assert lines[0] == "﻿" + ",".join(export.CSV_COLUMNS)
    assert lines[1].startswith("1,Raft: Understandable Consensus,Diego Ongaro; John Ousterhout,2014")
    assert lines[2].startswith("2,Second,")


def test_filename_slugs_query():
    assert export.filename('"Distributed Systems", Raft', "bibtex") == "distributed-systems-raft.bib"
    assert export.filename('""', "csv") == "results.csv"


def test_rebuild_abstract_orders_words_by_position():
    assert openalex.rebuild_abstract({"world": [1], "hello": [0, 2]}) == "hello world hello"
    assert openalex.rebuild_abstract(None) is None
    assert openalex.rebuild_abstract({}) is None

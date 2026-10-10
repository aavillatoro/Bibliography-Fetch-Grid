import pytest

from app import jobs
from app.models import Paper
from app.schemas import Paper as PaperSchema

W1 = "https://openalex.org/W1"
W2 = "https://openalex.org/W2"
MISSING = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def papers(session):
    jobs.upsert_papers(
        session,
        [
            PaperSchema(
                source_id=source_id,
                doi=None,
                title=title,
                publication_year=2020,
                cited_by_count=1,
                authors=["Ada Lovelace"],
            )
            for source_id, title in [(W1, "Paper One"), (W2, "Paper Two")]
        ],
    )
    session.commit()


def create(client, name="Thesis"):
    response = client.post("/collections", json={"name": name})
    assert response.status_code == 201
    return response.json()


def test_create_and_list_collections(client):
    created = create(client, "  Thesis ")
    create(client, "Background")

    assert created["name"] == "Thesis"
    assert created["paper_count"] == 0
    assert [c["name"] for c in client.get("/collections").json()] == ["Background", "Thesis"]


def test_collection_names_are_unique_and_not_blank(client):
    create(client)
    other = create(client, "Other")

    assert client.post("/collections", json={"name": "Thesis"}).status_code == 409
    assert client.post("/collections", json={"name": "   "}).status_code == 422
    assert (
        client.patch(f"/collections/{other['id']}", json={"name": "Thesis"}).status_code == 409
    )


def test_add_and_remove_papers(client, papers):
    url = f"/collections/{create(client)['id']}"

    assert client.post(f"{url}/papers", json={"source_ids": [W1, W2]}).json()["paper_count"] == 2
    # adding a paper twice is a no-op
    assert client.post(f"{url}/papers", json={"source_ids": [W1]}).json()["paper_count"] == 2
    assert [p["title"] for p in client.get(url).json()["papers"]] == ["Paper One", "Paper Two"]
    assert client.get("/collections").json()[0]["paper_count"] == 2

    removed = client.delete(f"{url}/papers", params={"source_id": [W1]})

    assert removed.json()["paper_count"] == 1
    assert [p["source_id"] for p in client.get(url).json()["papers"]] == [W2]


def test_add_rejects_unknown_papers(client, papers):
    url = f"/collections/{create(client)['id']}"

    response = client.post(f"{url}/papers", json={"source_ids": [W1, "https://openalex.org/W9"]})

    assert response.status_code == 404
    assert "W9" in response.json()["detail"]
    assert client.get(url).json()["papers"] == []


def test_rename_and_delete_collection(client, session, papers):
    url = f"/collections/{create(client)['id']}"
    client.post(f"{url}/papers", json={"source_ids": [W1]})

    assert client.patch(url, json={"name": "Chapter 2"}).json()["name"] == "Chapter 2"
    assert client.delete(url).status_code == 204
    assert client.get(url).status_code == 404
    # deleting a collection leaves the papers themselves alone
    assert session.query(Paper).count() == 2


def test_missing_collection_is_404(client):
    url = f"/collections/{MISSING}"

    assert client.get(url).status_code == 404
    assert client.patch(url, json={"name": "x"}).status_code == 404
    assert client.delete(url).status_code == 404
    assert client.post(f"{url}/papers", json={"source_ids": [W1]}).status_code == 404
    assert client.get(f"{url}/export").status_code == 404


def test_export_collection(client, papers):
    url = f"/collections/{create(client, 'Chapter 2')['id']}"
    client.post(f"{url}/papers", json={"source_ids": [W2]})

    response = client.get(f"{url}/export?format=ris")

    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="chapter-2.ris"'
    assert "TI  - Paper Two\n" in response.text
    assert "Paper One" not in response.text

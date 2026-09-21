from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
import httpx

app = FastAPI(title="Bibliography Fetch Grid")

jobs = {}
next_job_id = 1


class JobRequest(BaseModel):
    query: str


def process_job(job_id: int, query: str):
    jobs[job_id]["status"] = "processing"

    try:
        url = "https://api.openalex.org/works"

        params = {
            "search": query,
            "per-page": 25
        }

        response = httpx.get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()

        papers = []

        for work in data["results"]:
            papers.append({
                "title": work.get("title"),
                "year": work.get("publication_year"),
                "citations": work.get("cited_by_count")
            })

        papers.sort(
            key=lambda paper: paper["citations"] or 0,
            reverse=True
        )

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["papers_found"] = len(papers)
        jobs[job_id]["results"] = papers

    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)


@app.post("/jobs")
def create_job(request: JobRequest, background_tasks: BackgroundTasks):
    global next_job_id

    job_id = next_job_id
    next_job_id += 1

    jobs[job_id] = {
        "id": job_id,
        "query": request.query,
        "status": "queued"
    }

    background_tasks.add_task(
        process_job,
        job_id,
        request.query
    )

    return jobs[job_id]


@app.get("/jobs/{job_id}")
def get_job(job_id: int):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    return jobs[job_id]

@app.get("/jobs")
def get_jobs():
    return list(jobs.values())
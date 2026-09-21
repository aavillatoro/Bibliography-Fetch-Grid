from uuid import UUID

from fastapi import BackgroundTasks, FastAPI, HTTPException

from app import jobs
from app.schemas import JobDetail, JobRequest, JobSummary

app = FastAPI(title="Bibliography Fetch Grid")


@app.post("/jobs", response_model=JobSummary, status_code=202)
def create_job(request: JobRequest, background_tasks: BackgroundTasks):
    job = jobs.create_job(request)
    background_tasks.add_task(jobs.process_job, job.id, request)
    return job


@app.get("/jobs", response_model=list[JobSummary])
def list_jobs():
    return jobs.list_jobs()


@app.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: UUID):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

"""Fields stored for individual job postings."""
from typing import TypedDict


class JobRecord(TypedDict, total=False):
    record_type: str
    company_name: str
    ticker: str
    sector: str
    job_platform: str
    job_board_url: str
    job_url: str
    job_id: str
    job_title: str
    location: str
    employment_type: str
    description: str
    responsibilities: str
    requirements: str
    skills: list[str]
    technologies: list[str]
    education: list[str]
    is_cs_job: bool
    cs_relevance_score: float
    cs_filter_reason: str
    primary_category: str
    categories: list[str]
    is_ai_job: bool
    ai_signal: str
    crawl_date: str

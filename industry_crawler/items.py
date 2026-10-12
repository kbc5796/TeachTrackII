# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

from dataclasses import dataclass
from typing import TypedDict


@dataclass
class IndustryCrawlerItem:
    # define the fields for your item here like:
    # name: str | None = None
    pass


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

import asyncio
import csv
import json
from types import SimpleNamespace

import pytest
from scrapy import Request

from industry_crawler.company_input import read_companies
from industry_crawler.pipelines import JobPipeline
from industry_crawler.spiders.jobs import JobsSpider


def collect(spider):
    async def run():
        return [item async for item in spider.start()]
    return asyncio.run(run())


def write_sources(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["company_name", "ticker", "job_listing_url", "source_status"])
        writer.writeheader()
        writer.writerows(rows)


def test_incomplete_csv_crawls_unverified_and_reports_skips(tmp_path):
    path = tmp_path / "companies.csv"
    rows = [{"company_name": "Example", "ticker": "X", "job_listing_url": "https://jobs.lever.co/example",
             "source_status": "Unverified"},
            {"company_name": "Missing", "ticker": "M", "job_listing_url": "", "source_status": "Needs Review"},
            {"company_name": "Invalid", "ticker": "I", "job_listing_url": "javascript:bad", "source_status": ""},
            {"company_name": "Excluded", "ticker": "E", "job_listing_url": "https://example.org/jobs", "source_status": "Excluded"}]
    write_sources(path, rows)
    before = path.read_bytes()
    spider = JobsSpider(sources=path)
    output = collect(spider)
    assert isinstance(output[0], Request)
    assert len(output) == 4
    assert spider.source_summary["scheduled"] == 1
    assert spider.source_summary["missing_urls"] == 1
    assert spider.source_summary["invalid_urls"] == 1
    assert spider.source_summary["excluded"] == 1
    assert {r["ticker"] for r in spider.source_summary["skipped_companies"]} == {"M", "I", "E"}
    pipeline = JobPipeline()
    pipeline.crawler = SimpleNamespace(settings={"JOB_OUTPUT_DIR": str(tmp_path / "output")},
                                       stats=SimpleNamespace(get_stats=lambda: {}))
    pipeline.open_spider(spider)
    pipeline.close_spider(spider)
    summary = json.loads((tmp_path / "output/crawl_summary.json").read_text())
    assert summary["sources"] == spider.source_summary
    assert path.read_bytes() == before


def test_csv_quotes_extra_columns_and_no_network(tmp_path):
    path = tmp_path / "sources.csv"
    path.write_text('company_name,ticker,job_listing_url,notes\n"Example, Inc.",X,,"review next week"\n', encoding="utf-8-sig")
    row = next(read_companies(path))
    assert row["company_name"] == "Example, Inc."
    assert row["notes"] == "review next week"
    assert row["job_listing_url"] == ""


@pytest.mark.parametrize("header", ["company_name,ticker", "company_name,ticker,job_listing_url,ticker"])
def test_bad_header_becomes_input_error(tmp_path, header):
    path = tmp_path / "sources.csv"
    path.write_text(header + "\n")
    spider = JobsSpider(sources=path)
    assert collect(spider)[0]["record_type"] == "skipped"
    assert spider.source_summary["input_errors"] == 1


def test_malformed_row_does_not_stop_other_companies(tmp_path):
    path = tmp_path / "sources.csv"
    path.write_text("company_name,ticker,job_listing_url\nBad,B\nGood,G,https://jobs.lever.co/good\n")
    output = collect(JobsSpider(sources=path))
    assert output[0]["record_type"] == "skipped"
    assert isinstance(output[1], Request)


def test_missing_csv_is_reported(tmp_path):
    spider = JobsSpider(sources=tmp_path / "missing.csv")
    assert collect(spider)[0]["reason"] == "Company CSV not found"
    assert spider.source_summary["input_errors"] == 1


def test_known_invalid_status_skips_valid_looking_url(tmp_path):
    path = tmp_path / "sources.csv"
    write_sources(path, [{"company_name": "Bad", "ticker": "B", "job_listing_url": "https://example.org/jobs",
                          "source_status": "Invalid"}])
    spider = JobsSpider(sources=path)
    assert collect(spider)[0]["record_type"] == "skipped"
    assert spider.source_summary["excluded"] == 1

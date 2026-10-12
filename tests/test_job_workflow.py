"""Offline replay through CSV input, Workday callbacks, CS pipeline and exports."""
import asyncio
import csv
import json
from pathlib import Path
from types import SimpleNamespace

from scrapy import Request
from scrapy.exceptions import DropItem
from scrapy.http import TextResponse

from industry_crawler.pipelines import JobPipeline
from industry_crawler.spiders.jobs import JobsSpider


def test_csv_to_cs_exports_and_restart(tmp_path):
    root = Path(__file__).resolve().parents[1]
    original = (root / "company_sources.csv").read_bytes()
    with (root / "company_sources.csv").open(encoding="utf-8-sig", newline="") as f:
        row = next(r for r in csv.DictReader(f) if r["ticker"] == "NVDA")
    path = tmp_path / "sources.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["company_name", "ticker", "job_listing_url", "source_status", "sector"])
        writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in writer.fieldnames})
        writer.writerow({"company_name": "Missing fixture", "ticker": "MISS"})
    before = path.read_bytes()
    fixtures = json.loads((root / "tests/fixtures/captured_jobs.json").read_text(encoding="utf-8"))
    accepted = next(r for r in fixtures if r["ticker"] == "NVDA" and r["is_cs_job"])
    rejected = next(r for r in fixtures if not r["is_cs_job"])
    records = [accepted, rejected, accepted]
    stats = {}
    def increment(key):
        stats[key] = stats.get(key, 0) + 1
    for _ in range(2):
        spider = JobsSpider(sources=path, max_jobs=10)
        async def start():
            return [r async for r in spider.start()]
        scheduled = asyncio.run(start())
        request = next(r for r in scheduled if isinstance(r, Request))
        assert request.method == "POST" and "/wday/cxs/" in request.url
        pipeline = JobPipeline()
        pipeline.crawler = SimpleNamespace(settings={"JOB_OUTPUT_DIR": str(tmp_path / "output")},
            stats=SimpleNamespace(inc_value=increment, get_stats=lambda: stats))
        pipeline.open_spider(spider)
        for skip in scheduled:
            if not isinstance(skip, Request):
                try:
                    pipeline.process_item(skip, spider)
                except DropItem:
                    pass
        payload = {"total": 3, "jobPostings": [{"externalPath": f"/job/fixture/{i}"} for i in range(3)]}
        page = TextResponse(request.url, body=json.dumps(payload).encode(), encoding="utf-8", request=request)
        details = list(spider.parse_workday_list(page, **request.cb_kwargs))
        for detail, record in zip(details, records):
            info = {"title": record["job_title"], "jobDescription": record["description"],
                    "jobReqId": record.get("job_id") or record["job_title"],
                    "externalUrl": record["job_url"], "location": record.get("location", "")}
            page = TextResponse(detail.url, body=json.dumps({"jobPostingInfo": info}).encode(), encoding="utf-8", request=detail)
            item = next(spider.parse_workday_job(page, **detail.cb_kwargs))
            try:
                pipeline.process_item(item, spider)
            except DropItem:
                pass
        pipeline.close_spider(spider)
    output = tmp_path / "output"
    jobs = json.loads((output / "cs_jobs.json").read_text())
    assert len(jobs) == 1
    assert jobs[0]["is_cs_job"] and jobs[0]["technologies"] and jobs[0]["categories"]
    assert jobs[0]["company_name"] == row["company_name"]
    assert jobs[0]["description"] and jobs[0]["crawl_date"]
    failures = list(csv.DictReader((output / "crawl_failures.csv").open(encoding="utf-8")))
    assert any(f["reason"].startswith("non-CS:") for f in failures)
    summary = json.loads((output / "crawl_summary.json").read_text())
    assert summary["sources"]["missing_urls"] == 1
    assert stats["jobs/duplicate_updates"] == 3
    assert all((output / p).exists() for p in ["jobs.sqlite3", "cs_jobs.csv", "cs_jobs.jsonl"])
    assert path.read_bytes() == before
    assert (root / "company_sources.csv").read_bytes() == original

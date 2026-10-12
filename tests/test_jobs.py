import csv
import json
from types import SimpleNamespace

import pytest
from scrapy import Request
from scrapy.exceptions import DropItem
from scrapy.http import HtmlResponse, TextResponse

from industry_crawler.cs_filter import score_job
from industry_crawler.normalization import canonical_url, job_identity
from industry_crawler.pipelines import JobPipeline
from industry_crawler.spiders.jobs import JobsSpider
from industry_crawler.spiders.skill_categories import analyze_job
from industry_crawler.storage import JobStore

ROW = {"company_name": "Fixture Company", "ticker": "FIX", "job_listing_url": "https://example.org/jobs"}
BODY = "Responsibilities\nSoftware development in Python and AWS.\nRequirements\nComputer science degree and SQL."


def response(payload, url="https://example.org/api"):
    return TextResponse(url, body=json.dumps(payload).encode(), encoding="utf-8", request=Request(url))


@pytest.mark.parametrize("title", ["Software Engineer", "AI Research Scientist", "Data Engineer",
                                  "DevOps Engineer", "Network Engineer", "IT Systems Analyst",
                                  "QA Automation Engineer", "Embedded Software Engineer",
                                  "Machine Learning Intern", "Penetration Tester"])
def test_cs_titles(title):
    assert score_job(title, BODY)["is_cs_job"]


@pytest.mark.parametrize("title", ["Cashier", "Sales Associate", "Nurse", "Accountant",
                                  "Mechanical Engineer", "Marketing Analyst", "HR Manager"])
def test_non_cs(title):
    assert not score_job(title, "We use Python and AI technology.")["is_cs_job"]


def test_ambiguous_and_threshold():
    assert not score_job("Technical Support Engineer", "Answer customer calls")["is_cs_job"]
    assert score_job("Technical Support Engineer", BODY)["is_cs_job"]
    assert not score_job("Software Engineer", "", threshold=0.9)["is_cs_job"]
    with pytest.raises(ValueError):
        score_job("Software Engineer", BODY, threshold=2)


def test_keywords_categories_sections():
    result = analyze_job("Machine Learning Engineer", BODY + "\nPyTorch TensorFlow Docker Kubernetes C++ CI/CD")
    assert "AI" in result["categories"]
    assert result["is_ai_job"]
    assert {"python", "aws", "c++", "ci/cd"} <= set(result["technologies"])
    assert "Software development" in result["responsibilities"]
    assert "Computer science" in result["requirements"]
    result = analyze_job("", "JavaScript typython reactivate github")
    assert "java" not in result["technologies"]
    assert "python" not in result["technologies"]
    assert "react" not in result["technologies"]
    assert "git" not in result["technologies"]


def test_workday_details_and_pagination():
    spider = JobsSpider(max_jobs=50)
    result = list(spider.parse_workday_list(response({"total": 21, "jobPostings": [
        {"externalPath": "/job/engineer/123"}]}), "https://example.org/wday/cxs/test/board", 0, 0, ROW, "Workday"))
    assert len(result) == 2
    assert json.loads(result[1].body)["offset"] == 20
    item = next(spider.parse_workday_job(response({"jobPostingInfo": {"title": "Software Engineer",
        "jobDescription": BODY, "jobReqId": "123", "timeType": "Full time"}}), ROW, "Workday", "/job/123"))
    assert item["job_id"] == "123"
    assert "/wday/cxs/" not in item["job_url"]
    assert item["job_board_url"] == ROW["job_listing_url"]


def test_greenhouse():
    item = next(JobsSpider().parse_greenhouse(response({"jobs": [{"id": 1, "title": "Software Engineer",
        "absolute_url": "https://example.org/jobs/1", "content": BODY, "location": {"name": "Remote"}}]}), ROW, "Greenhouse"))
    assert item["job_id"] == "1" and item["location"] == "Remote"


def test_lever_and_pagination():
    job = {"id": "abc", "text": "Software Engineer", "descriptionPlain": BODY,
           "hostedUrl": "https://jobs.lever.co/example/abc", "categories": {"commitment": "Internship"}}
    spider = JobsSpider(max_jobs=150)
    items = list(spider.parse_lever(response([job] * 100, "https://api.lever.co/v0/postings/example?skip=0&limit=100"), ROW, "Lever"))
    assert items[0]["employment_type"] == "Internship"
    assert isinstance(items[-1], Request) and "skip=100" in items[-1].url


@pytest.mark.parametrize("graph", [False, True])
def test_custom_structured(graph):
    data = {"@type": "JobPosting", "title": "Software Engineer", "description": BODY,
            "identifier": {"value": "123"}, "jobLocation": {"address": {"addressLocality": "Chicago"}}}
    if graph:
        data = {"@graph": [data]}
    html = '<body><script type="application/ld+json">' + json.dumps(data) + '</script></body>'
    page = HtmlResponse("https://example.org/jobs/123", body=html.encode(), encoding="utf-8")
    item = next(JobsSpider().parse_page(page, ROW, "Custom", "example.org", 0))
    assert item["record_type"] == "job" and item["job_id"] == "123"


def test_board_is_not_job():
    page = HtmlResponse("https://example.org/careers", body=b"<body><h1>Careers</h1>Join our technology team</body>", encoding="utf-8")
    item = next(JobsSpider().parse_page(page, ROW, "Custom", "example.org", 0))
    assert item["record_type"] == "job_board"


def test_html_job():
    page = HtmlResponse("https://example.org/jobs/123", body=("<body><h1>Software Engineer</h1><h2>Responsibilities</h2><p>" +
        "Software development in Python and distributed systems. " * 4 + "</p><h2>Requirements</h2><p>Computer science degree.</p></body>").encode(), encoding="utf-8")
    assert next(JobsSpider().parse_page(page, ROW, "Custom", "example.org", 0))["record_type"] == "job"


def test_identity():
    item = {"ticker": "X", "job_platform": "Lever", "job_id": "1", "job_url": "https://example.org/jobs/1"}
    assert job_identity(item) == job_identity({**item, "job_url": "https://other.org/1"})
    assert job_identity(item) != job_identity({**item, "job_id": "2"})
    assert job_identity(item) != job_identity({**item, "ticker": "Y"})
    assert job_identity({"job_title": "Software  Engineer", "location": "Remote"}) == job_identity(
        {"job_title": "software engineer", "location": "remote"})


@pytest.mark.parametrize("url", ["javascript:alert(1)", "", "https:///jobs", "https://user:pass@example.org"])
def test_invalid_urls(url):
    assert canonical_url(url) == ""


def test_storage_restart_exports(tmp_path):
    store = JobStore(tmp_path)
    item = {"job_id": "1", "ticker": "X", "job_title": "Engineer, software", "technologies": ["python"]}
    assert store.save(item)
    store.close()

    store = JobStore(tmp_path)
    assert not store.save(item)
    store.reject(item, "fixture rejection")
    store.export()
    assert len(json.loads((tmp_path / "cs_jobs.json").read_text())) == 1
    assert json.loads((tmp_path / "cs_jobs.jsonl").read_text())["technologies"] == ["python"]
    with (tmp_path / "cs_jobs.csv").open(newline="") as f:
        exported = next(csv.DictReader(f))
    assert exported["job_title"] == "Engineer, software"
    assert json.loads(exported["technologies"]) == ["python"]
    store.close()


def test_url_dedup_without_id_preserves_distinct_ids(tmp_path):
    store = JobStore(tmp_path)
    item = {"company_name": "Example", "ticker": "X", "job_platform": "Lever",
            "job_url": "https://example.org/job/1", "job_id": "1"}
    assert store.save(item)
    assert not store.save({**item, "job_id": ""})
    assert store.save({**item, "job_id": "2"})
    assert store.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 2
    store.close()


def test_pipeline_rejections_and_duplicates(tmp_path):
    pipeline = JobPipeline()
    pipeline.crawler = SimpleNamespace(settings={"JOB_OUTPUT_DIR": str(tmp_path)},
                                       stats=SimpleNamespace(inc_value=lambda _: None))
    pipeline.open_spider(None)
    item = JobsSpider().make_item(ROW, "Custom", "https://example.org/jobs/1", "Software Engineer", BODY)
    assert pipeline.process_item(dict(item), None)["is_cs_job"]
    for bad in [dict(item), {**item, "job_url": "bad"}, {**item, "description": ""},
                {**item, "job_title": "Cashier"}, {"record_type": "job_board"}]:
        with pytest.raises(DropItem):
            pipeline.process_item(bad, None)
    pipeline.store.close()


def test_sources_status_and_fallback(tmp_path):
    import asyncio
    source = tmp_path / "sources.csv"
    source.write_text("company_name,ticker,career_site_url,job_listing_url,source_status,extra\n"
                      "Test,T,https://example.org/careers,,Unverified,ignored\n")
    async def collect(spider):
        return [item async for item in spider.start()]
    assert asyncio.run(collect(JobsSpider(sources=source, source_status="Verified"))) == []
    assert asyncio.run(collect(JobsSpider(sources=source)))[0]["record_type"] == "skipped"
    assert isinstance(asyncio.run(collect(JobsSpider(sources=source, career_fallback="true")))[0], Request)


def test_callback_failure_is_quarantined():
    from industry_crawler.job_errors import JobErrorMiddleware
    request = Request("https://example.org/api", cb_kwargs={"row": ROW})
    page = TextResponse(request.url, request=request)
    item = JobErrorMiddleware().process_spider_exception(page, ValueError("Malformed API"), None)[0]
    assert item["record_type"] == "skipped"
    assert item["ticker"] == "FIX" and item["reason"] == "ValueError"

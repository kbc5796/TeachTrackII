"""Offline adapter contract, identity conflict, migration and source-policy tests."""
import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
from scrapy import Request
from scrapy.exceptions import IgnoreRequest
from scrapy.http import TextResponse
from scrapy.utils.request import request_from_dict

from industry_crawler.adapters import ADAPTERS, get_adapter
from industry_crawler.adapters.html import HTMLAdapter
from industry_crawler.job_errors import JobErrorMiddleware
from industry_crawler.normalization import canonical_url, normalize_job
from industry_crawler.source_policy import SourcePolicyMiddleware
from industry_crawler.spiders.jobs import JobsSpider
from industry_crawler.spiders.platforms import detect_platform
from industry_crawler.storage import JobStore

ROW = {"ticker": "FIX", "company_name": "Fixture", "job_listing_url": "https://careers.smartrecruiters.com/Fixture"}
JOB = {"ticker": "FIX", "company_name": "Fixture", "job_platform": "Lever", "job_id": "1",
       "job_url": "https://example.org/jobs/1", "job_title": "Software Engineer", "location": "Austin, Texas",
       "crawl_date": "2026-10-01T00:00:00+00:00", "description": "Python software engineering"}


def response(data, url="https://api.smartrecruiters.com/v1/companies/Fixture/postings"):
    return TextResponse(url, body=json.dumps(data).encode(), encoding="utf-8", request=Request(url, cb_kwargs={"row": ROW}))


def saved(store):
    return [json.loads(r[0]) for r in store.db.execute("SELECT payload FROM jobs")]


def test_registry_and_checkpoint():
    spider = JobsSpider()
    assert isinstance(get_adapter("Custom", spider), HTMLAdapter)
    urls = {"Workday": "https://fixture.wd5.myworkdayjobs.com/en-US/External",
            "Greenhouse": "https://boards.greenhouse.io/fixture", "Lever": "https://jobs.lever.co/fixture",
            "SmartRecruiters": ROW["job_listing_url"], "Custom": "https://example.org/jobs"}
    for platform, url in urls.items():
        request = get_adapter(platform, spider).start(ROW, url, platform)
        restored = request_from_dict(request.to_dict(spider=spider), spider=spider)
        assert restored.callback == request.callback
        assert restored.errback == spider.on_error
        assert not restored.meta.get("playwright")


def test_registry_extension_does_not_require_spider_changes(monkeypatch):
    class ExampleAdapter(HTMLAdapter):
        pass
    monkeypatch.setitem(ADAPTERS, "Example", ExampleAdapter)
    assert isinstance(JobsSpider().adapter("Example"), ExampleAdapter)


def test_smartrecruiters_list_details_and_limits():
    spider = JobsSpider(max_jobs=3)
    adapter = spider.adapter("SmartRecruiters")
    result = list(adapter.parse_list(response({"content": [{"id": "123", "ref": "https://evil.org"}],
                                               "limit": 1, "totalFound": 2}), ROW, "SmartRecruiters", "Fixture", 0))
    assert result[0].url.endswith("/postings/123")
    assert "offset=1" in result[1].url
    item = next(spider.parse_api(response({"id": "123", "name": "Software Engineer", "refNumber": "REQ1",
        "postingUrl": "https://jobs.smartrecruiters.com/Fixture/123-engineer", "company": {"name": "Fixture Inc"},
        "location": {"city": "Austin", "region": "Texas", "country": "us", "remote": True},
        "typeOfEmployment": {"label": "Full time"}, "jobAd": {"sections": {
            "jobDescription": {"title": "Responsibilities", "text": "<p>Python development</p>"},
            "qualifications": {"title": "Requirements", "text": "CS degree"},
            "additionalInformation": {"text": "Full benefits"}}}}), ROW, "SmartRecruiters", "parse_job"))
    assert item["source_job_id"] == "123" and item["requisition_id"] == "REQ1"
    assert item["employment_type"] == "Full-time"
    assert "Python development" in item["description"] and "Full benefits" in item["description"]
    assert item["location"] == "Austin, TX, us; Remote"
    assert item["source_employer"] == {"name": "Fixture Inc"}
    spider.counts[spider.key(ROW)] = 3
    assert list(adapter.parse_list(response({"content": [{"id": "124"}], "totalFound": 5}), ROW,
                                   "SmartRecruiters", "Fixture", 0)) == []


@pytest.mark.parametrize("platform,payload,method,kwargs", [
    ("Workday", [], "parse_workday_list", {"api": "https://example.org/api", "offset": 0, "total": 0}),
    ("Workday", {"jobPostings": None}, "parse_workday_list", {"api": "https://example.org/api", "offset": 0, "total": 0}),
    ("Greenhouse", {"jobs": {}}, "parse_greenhouse", {}),
    ("Lever", {}, "parse_lever", {}),
    ("SmartRecruiters", {"content": None}, "parse_list", {"company": "Fixture", "offset": 0}),
    ("SmartRecruiters", {"errors": ["denied"]}, "parse_list", {"company": "Fixture", "offset": 0}),
    ("SmartRecruiters", {"content": [], "limit": -1}, "parse_list", {"company": "Fixture", "offset": 0}),
])
def test_malformed_api_quarantine(platform, payload, method, kwargs):
    spider = JobsSpider()
    page = response(payload)
    with pytest.raises((ValueError, TypeError)) as failure:
        list(getattr(spider.adapter(platform), method)(page, row=ROW, platform=platform, **kwargs))
    rejected = JobErrorMiddleware().process_spider_exception(page, failure.value, spider)[0]
    assert rejected["record_type"] == "skipped" and rejected["ticker"] == "FIX"


@pytest.mark.parametrize("platform,method", [("Greenhouse", "parse_greenhouse"), ("Lever", "parse_lever"),
                                            ("SmartRecruiters", "parse_job")])
def test_bad_json(platform, method):
    page = TextResponse("https://example.org", body=b"not json", encoding="utf-8")
    with pytest.raises(ValueError):
        list(getattr(JobsSpider().adapter(platform), method)(page, ROW, platform))


def test_malformed_entries_do_not_drop_following_jobs():
    adapter = JobsSpider().adapter("SmartRecruiters")
    result = list(adapter.parse_list(response({"content": [None, {}, {"id": "123"}]}), ROW,
                                     "SmartRecruiters", "Fixture", 0))
    assert len(result) == 3 and isinstance(result[-1], Request)


def test_missing_smart_fields_and_inactive():
    adapter = JobsSpider().adapter("SmartRecruiters")
    item = next(adapter.parse_job(response({}), ROW, "SmartRecruiters"))
    assert item["job_title"] == item["description"] == item["job_url"] == ""
    assert next(adapter.parse_job(response({"active": False}), ROW, "SmartRecruiters"))["record_type"] == "skipped"


def test_normalization_preserves_originals():
    item = normalize_job({**JOB, "ticker": " fix.a ", "company_name": " Fixture  Inc ",
                          "job_title": " Software  Engineer ", "employment_type": "FULL_TIME",
                          "job_url": JOB["job_url"] + "?utm_source=test&gh_jid=1#apply"})
    assert item["ticker"] == "FIX-A" and item["company_name"] == "Fixture Inc"
    assert item["location"] == "Austin, TX" and item["employment_type"] == "Full-time"
    assert item["original_values"]["location"] == "Austin, Texas"
    assert item["job_url"].endswith("?gh_jid=1")
    assert normalize_job(item) == item
    assert normalize_job({"location": "London, Ontario, Canada"})["location"] == "London, Ontario, Canada"
    assert normalize_job({"job_title": {}, "employment_type": ["FULL_TIME", "PART_TIME"]})["employment_type"] == "Full-time, Part-time"


def test_same_titles_conflicting_ids_and_companies(tmp_path):
    store = JobStore(tmp_path)
    assert store.save(JOB)
    assert store.save({**JOB, "job_id": "2"})
    assert store.save({**JOB, "ticker": "OTHER"})
    assert len(saved(store)) == 3
    assert saved(store)[1]["duplicate_review"]
    store.close()


def test_cross_source_verified_requisition_and_history_restart(tmp_path):
    store = JobStore(tmp_path)
    store.save({**JOB, "requisition_id": "REQ1"})
    store.close()
    store = JobStore(tmp_path)
    new = {**JOB, "job_platform": "Greenhouse", "job_id": "gh1", "job_url": "https://example.org/jobs/gh1",
           "requisition_id": "REQ1", "crawl_date": "2026-10-27T00:00:00+00:00"}
    assert not store.save(new)
    assert not store.save({**JOB, "requisition_id": "REQ1"})
    item = saved(store)[0]
    assert len(item["source_observations"]) == 2
    assert item["first_seen"] == JOB["crawl_date"] and item["last_seen"] == new["crawl_date"]
    store.close()


def test_cross_source_url_requires_nonconflicting_evidence(tmp_path):
    store = JobStore(tmp_path)
    store.save(JOB)
    assert store.save({**JOB, "job_platform": "Greenhouse", "job_id": "gh1"})
    assert len(saved(store)) == 2
    # Two possible matches: keep the ambiguous capture, flag instead of deleting.
    assert store.save({**JOB, "job_platform": "Custom", "job_id": ""})
    assert len(saved(store)) == 3 and len(saved(store)[-1]["duplicate_review"]) == 2
    store.close()


def test_confirmed_ids_override_reused_req_and_url(tmp_path):
    store = JobStore(tmp_path)
    store.save({**JOB, "requisition_id": "REQ1"})
    assert store.save({**JOB, "job_id": "2", "requisition_id": "REQ1"})
    assert store.save({**JOB, "job_platform": "Custom", "job_id": "", "requisition_id": "REQ2"})
    assert len(saved(store)) == 3
    store.close()


def test_url_only_cross_source_and_no_identity_no_title_merge(tmp_path):
    store = JobStore(tmp_path)
    store.save(JOB)
    assert not store.save({**JOB, "job_platform": "Custom", "job_id": ""})
    assert store.save({**JOB, "job_url": "", "job_id": ""})
    assert store.save({**JOB, "job_url": "", "job_id": ""})
    assert len(saved(store)) == 3
    store.close()


def test_legacy_database_migration_is_additive(tmp_path):
    db = sqlite3.connect(tmp_path / "jobs.sqlite3")
    db.execute("CREATE TABLE jobs (identity TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    payload = json.dumps({**JOB, "ticker": " fix "})
    db.execute("INSERT INTO jobs VALUES (?, ?)", ("legacy-key", payload))
    db.commit()
    db.close()
    store = JobStore(tmp_path)
    assert store.db.execute("SELECT payload FROM jobs").fetchone()[0] == payload
    assert not store.save({**JOB, "job_url": "https://example.org/new-url"})
    assert store.db.execute("SELECT identity FROM jobs").fetchone()[0] == "legacy-key"
    assert len(saved(store)) == 1
    store.export()
    assert json.loads((tmp_path / "cs_jobs.json").read_text())[0]["first_seen"] == JOB["crawl_date"]
    store.close()


@pytest.mark.parametrize("url", ["http://127.0.0.1/jobs", "http://[::1]/jobs", "http://10.0.0.1/jobs",
                                  "http://localhost/jobs", "https://example.org:444/jobs", "file:///jobs"])
def test_unsafe_sources(url):
    assert not canonical_url(url)


def test_spoofed_provider_host():
    assert detect_platform("https://notlever.co/x") == "Custom"
    assert detect_platform("https://jobs.smartrecruiters.com.evil.org/x") == "Custom"
    assert detect_platform(ROW["job_listing_url"]) == "SmartRecruiters"


def test_source_policy_and_redirects():
    middleware = SourcePolicyMiddleware()
    spider = JobsSpider()
    middleware.process_request(Request("https://api.smartrecruiters.com/v1/companies/Fixture/postings", cb_kwargs={"row": ROW}), spider)
    for url in ["https://evil.org/jobs", "http://127.0.0.1/jobs"]:
        with pytest.raises(IgnoreRequest):
            middleware.process_request(Request(url, cb_kwargs={"row": ROW}), spider)


def test_unapproved_csv_is_not_crawled(tmp_path):
    source = tmp_path / "unapproved.csv"
    source.write_text("company_name,ticker,job_listing_url\nFixture,FIX,https://evil.org/jobs\n")
    async def collect():
        return [item async for item in JobsSpider(sources=source).start()]
    result = asyncio.run(collect())
    assert result[0]["reason"] == "unapproved source URL"


def test_http_error_retains_company_context():
    failure = SimpleNamespace(request=Request("https://example.org", cb_kwargs={"row": ROW}), value=TimeoutError("timed out"))
    item = next(JobsSpider().on_error(failure))
    assert item["ticker"] == "FIX" and item["reason"] == "TimeoutError"


def test_strong_id_wins_after_ambiguous_capture_and_repeated_review(tmp_path):
    store = JobStore(tmp_path)
    store.save(JOB)
    store.save({**JOB, "job_platform": "Greenhouse", "job_id": "gh1"})
    capture = {**JOB, "job_platform": "Custom", "job_id": ""}
    assert store.save(capture)
    assert not store.save(capture)
    assert not store.save(JOB)
    assert len(saved(store)) == 3
    store.close()


def test_item_factory_preserves_original_title_and_url():
    item = JobsSpider().make_item(ROW, "Custom", "https://example.org/job/1?utm_source=test",
                                  " Software  Engineer ", "Full description")
    assert item["original_values"]["job_title"] == " Software  Engineer "
    assert item["source_url"].endswith("?utm_source=test")


def test_malformed_nested_fields_and_following_job():
    spider = JobsSpider()
    good = {"id": "1", "text": "Software Engineer", "descriptionPlain": "Python", "hostedUrl": "https://example.org/job/1"}
    items = list(spider.parse_lever(response([{**good, "categories": [], "lists": [None], "additionalPlain": {}}, good]), ROW, "Lever"))
    assert len(items) == 2 and items[-1]["job_id"] == "1"
    assert next(spider.parse_workday_job(response({"jobPostingInfo": []}), ROW, "Workday"))["record_type"] == "skipped"
    item = next(spider.adapter("SmartRecruiters").parse_job(response({"jobAd": [], "location": [],
                               "typeOfEmployment": []}), ROW, "SmartRecruiters"))
    assert item["description"] == item["location"] == ""


def test_unapproved_career_host_cannot_authorize_redirect():
    with pytest.raises(IgnoreRequest):
        SourcePolicyMiddleware().process_request(Request("https://evil.org/jobs", cb_kwargs={
            "row": {**ROW, "career_site_url": "https://evil.org"}}), JobsSpider())
    with pytest.raises(IgnoreRequest):
        SourcePolicyMiddleware().process_request(Request("https://evil.org/robots.txt",
            meta={"dont_obey_robotstxt": True}), JobsSpider())

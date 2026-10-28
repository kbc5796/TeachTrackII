from importlib.util import find_spec
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import scrapy

from ..company_input import read_companies
from ..normalization import canonical_url, normalize_job, clean_text
from ..adapters import get_adapter
from ..items import JobRecord
from .skill_categories import analyze_job
from .platforms import detect_platform

# Browser rendering is optional and only used for "Custom" career sites.
HAVE_PLAYWRIGHT = find_spec("scrapy_playwright") is not None


# company_sources.csv lives in the outer project folder.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CSV_FILE = PROJECT_ROOT / "company_sources.csv"

# On custom sites only follow links that look job-related.
LINK_HINTS = ["job", "career", "position", "opening", "requisition", "search"]
SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".mp4", ".mp3")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class JobsSpider(scrapy.Spider):
    """
    Reads company_sources.csv and collects one record per job.

    Workday / Greenhouse / Lever / SmartRecruiters use JSON APIs without a browser.
    Custom HTML uses JobPosting data or strong individual-job indicators.
    Playwright requires an explicit render=true option.

    Test small first:
        scrapy crawl jobs -a only=NVDA,MSFT -a max_jobs=20 -O data/sample/jobs.json
    Full run:
        scrapy crawl jobs -O data/jobs.json
    """
    name = "jobs"

    custom_settings = {
        # --- responsible crawling (research doc, sections 6 and 7) ---
        "ROBOTSTXT_OBEY": True,
        "DOWNLOADER_MIDDLEWARES": {"industry_crawler.source_policy.SourcePolicyMiddleware": 40,
                                   "industry_crawler.retry.BackoffMiddleware": 550},
        "ITEM_PIPELINES": {"industry_crawler.pipelines.JobPipeline": 300},
        "SPIDER_MIDDLEWARES": {"industry_crawler.job_errors.JobErrorMiddleware": 550},
        # Limits are per company in this spider (max_jobs / max_pages),
        # not a global page count, so these two are intentionally off.
        "DEPTH_LIMIT": 0,
        "CLOSESPIDER_PAGECOUNT": 0,
    }
    if HAVE_PLAYWRIGHT:
        custom_settings.update({
            "DOWNLOAD_HANDLERS": {
                "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
                "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            },
            "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
            "PLAYWRIGHT_BROWSER_TYPE": "chromium",
            "PLAYWRIGHT_LAUNCH_OPTIONS": {"headless": True},
            "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": 4,
            "CONCURRENT_REQUESTS": 4,
        })

    def __init__(self, only=None, max_jobs=200, max_pages=30, sources=None,
                 source_status=None, render="false", *args, **kwargs):
        super().__init__(*args, **kwargs)
        # only="MSFT,NVDA" limits the run to those tickers (for testing).
        self.only = {t.strip().upper() for t in only.split(",")} if only else None
        self.max_jobs = int(max_jobs)      # jobs per company (API platforms)
        self.max_pages = int(max_pages)    # pages per company (custom sites)
        self.counts = defaultdict(int)
        self.pages = defaultdict(int)
        self.seen_pages = defaultdict(set)
        self.sources = Path(sources) if sources else CSV_FILE
        self.statuses = {s.strip().lower() for s in source_status.split(",")} if source_status else None
        self.render = str(render).lower() == "true"
        self.source_summary = {"selected": 0, "scheduled": 0, "missing_urls": 0,
                               "invalid_urls": 0, "excluded": 0, "input_errors": 0,
                               "skipped_companies": []}
        if self.render and not HAVE_PLAYWRIGHT:
            raise ValueError("Install scrapy-playwright to use render=true")
        if self.max_jobs <= 0 or self.max_pages <= 0:
            raise ValueError("Crawl limits must be positive")

    # ------------------------------------------------------------------
    # Start: read the CSV and route each company by platform.
    # ------------------------------------------------------------------
    async def start(self):
        if not self.sources.exists():
            self.logger.error("CSV file not found: %s", self.sources)
            yield self.skip_source({}, "Company CSV not found", "input_errors")
            return

        try:
            rows = list(read_companies(self.sources))
        except (ValueError, OSError, UnicodeError) as exc:
            yield self.skip_source({}, f"Cannot read company CSV: {exc}", "input_errors")
            return

        try:
            approved = {(self.key(r).upper(), canonical_url(r.get("job_listing_url", "")))
                        for r in read_companies(CSV_FILE)
                        if not r.get("input_error") and
                        r.get("source_status", "").strip().lower() not in {"invalid", "excluded"} and
                        r.get("excluded", "").strip().lower() not in {"true", "1", "yes"}}
        except (ValueError, OSError, UnicodeError) as exc:
            yield self.skip_source({}, f"Cannot read approved company CSV: {exc}", "input_errors")
            return

        for row in rows:
            ticker = (row.get("ticker") or "").strip().upper()
            if self.only and ticker not in self.only:
                continue

            self.source_summary["selected"] += 1
            if row.get("input_error") or not row.get("company_name", "").strip() or not ticker:
                yield self.skip_source(row, row.get("input_error") or "Missing company name or ticker", "input_errors")
                continue
            status = row.get("source_status", "").strip().lower()
            if status in {"excluded", "invalid"} or row.get("excluded", "").strip().lower() in {"true", "1", "yes"}:
                yield self.skip_source(row, "Source explicitly excluded or marked Invalid", "excluded")
                continue
            if self.statuses and row.get("source_status", "").lower() not in self.statuses:
                yield self.skip_source(row, "Excluded by requested source_status filter", "excluded")
                continue

            url = (row.get("job_listing_url") or "").strip()
            if not url:
                yield self.skip_source(row, "Missing job-listing URL", "missing_urls")
                continue
            if not canonical_url(url):
                yield self.skip_source(row, "Invalid source URL syntax", "invalid_urls")
                continue

            if (self.key(row).upper(), canonical_url(url)) not in approved:
                yield self.skip_source(row, "unapproved source URL", "invalid_urls")
                continue

            self.source_summary["scheduled"] += 1
            platform = detect_platform(url)
            request = self.adapter(platform).start(row, url, platform)
            if request is None:
                request = self.adapter("Custom").start(row, url, platform)
            yield request

    # ------------------------------------------------------------------
    # Local input skips (no network source verification)
    # ------------------------------------------------------------------
    def skip_source(self, row, reason, kind):
        entry = {"company_name": row.get("company_name", ""), "ticker": row.get("ticker", ""),
                 "source_url": row.get("job_listing_url", ""), "reason": reason}
        self.source_summary[kind] += 1
        self.source_summary["skipped_companies"].append(entry)
        self.logger.warning("Skipping %s: %s", entry["ticker"] or "source CSV", reason)
        return {**row, **entry, "record_type": "skipped"}

    def adapter(self, platform):
        return get_adapter(platform, self)

    # ------------------------------------------------------------------
    # Workday (JSON API)
    # ------------------------------------------------------------------
    def workday_page(self, api, offset, total, row, platform):
        return self.adapter("Workday").workday_page(api, offset, total, row, platform)

    def parse_workday_list(self, response, api, offset, total, row, platform):
        return self.adapter("Workday").parse_workday_list(response, api, offset, total, row, platform)

    def parse_workday_job(self, response, row, platform, external_path=""):
        return self.adapter("Workday").parse_workday_job(response, row, platform, external_path)

    def parse_greenhouse(self, response, row, platform):
        return self.adapter("Greenhouse").parse_greenhouse(response, row, platform)

    def parse_lever(self, response, row, platform):
        return self.adapter("Lever").parse_lever(response, row, platform)

    def parse_page(self, response, row, platform, host, depth):
        return self.adapter("Custom").parse_page(response, row, platform, host, depth)

    def parse_api(self, response, row, platform, action, **kwargs):
        # Named spider callback keeps requests compatible with JOBDIR serialization.
        if action not in {"parse_list", "parse_job"}:
            raise ValueError("Unknown adapter action")
        return getattr(self.adapter(platform), action)(response, row, platform, **kwargs)

    def render_meta(self):
        if not self.render:
            return {}
        return {"playwright": True,
                "playwright_page_goto_kwargs": {"wait_until": "domcontentloaded"}}

    @staticmethod
    def key(row):
        return (row.get("ticker") or row.get("company_name") or "").strip()

    def make_item(self, row, platform, url, title, text, location="", job_id="", employment_type="") -> JobRecord:
        text = text if isinstance(text, str) else ""
        url = url if isinstance(url, str) else ""
        a = analyze_job(clean_text(title), text)
        return normalize_job({
            "record_type": "job",
            "company_name": row.get("company_name", ""),
            "ticker": row.get("ticker", ""),
            "sector": row.get("sector", ""),
            "domain": urlparse(url).netloc,
            "job_platform": platform,
            "source_url": url,
            "job_url": url,
            "job_board_url": row.get("job_listing_url", ""),
            "job_id": job_id,
            "employment_type": employment_type,
            "job_title": title,
            "location": location,
            "primary_category": a["primary_category"],
            "categories": a["categories"],
            "is_ai_job": a["is_ai_job"],
            "ai_signal": a["ai_signal"],
            "skills": a["skills"],
            "technologies": a["technologies"],
            "education": a["education"],
            "responsibilities": a["responsibilities"],
            "requirements": a["requirements"],
            "description": text,
            "crawl_date": now_iso(),
        })

    def on_error(self, failure):
        """Record anything we could not collect (robots.txt block, 404,
        timeout...) so the limitations can be documented."""
        request = failure.request
        row = request.cb_kwargs.get("row", {})
        yield {
            "record_type": "skipped",
            "company_name": row.get("company_name", ""),
            "ticker": row.get("ticker", ""),
            "source_url": request.url,
            "reason": failure.value.__class__.__name__,   # e.g. IgnoreRequest = robots.txt
            "detail": str(failure.value)[:200],
            "crawl_date": now_iso(),
        }

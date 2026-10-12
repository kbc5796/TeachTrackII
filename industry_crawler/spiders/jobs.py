import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import scrapy

try:
    from .skill_categories import analyze_job
    from .platforms import (
        detect_platform, workday_api_base, greenhouse_board,
        lever_company, html_to_text, page_text,
    )
except ImportError:                      # if the files sit outside a package
    from skill_categories import analyze_job
    from platforms import (
        detect_platform, workday_api_base, greenhouse_board,
        lever_company, html_to_text, page_text,
    )

# Browser rendering is optional and only used for "Custom" career sites.
try:
    from scrapy_playwright.page import PageMethod
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False


# company_sources.csv lives in the outer project folder.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CSV_FILE = PROJECT_ROOT / "company_sources.csv"

JOB_SIGNALS = [
    "job", "jobs", "career", "careers", "position", "requisition",
    "responsibilities", "qualifications", "requirements",
]
# On custom sites only follow links that look job-related.
LINK_HINTS = ["job", "career", "position", "opening", "requisition", "search"]
SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".mp4", ".mp3")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class JobsSpider(scrapy.Spider):
    """
    Reads company_sources.csv and collects one record per job.

    Workday / Greenhouse / Lever  -> their JSON APIs (no browser).
    Everything else (Custom)      -> normal HTML, rendered with Playwright
                                     if scrapy-playwright is installed.

    Test small first:
        scrapy crawl jobs -a only=NVDA,MSFT -a max_jobs=20 -O test.json
    Full run:
        scrapy crawl jobs -O jobs.json
    """
    name = "jobs"

    custom_settings = {
        # --- responsible crawling (research doc, sections 6 and 7) ---
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 1,
        "AUTOTHROTTLE_MAX_DELAY": 5,
        "USER_AGENT": "TeachTrackBot (university research; contact: YOUR_EMAIL_HERE)",
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

    def __init__(self, only=None, max_jobs=200, max_pages=30, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # only="MSFT,NVDA" limits the run to those tickers (for testing).
        self.only = {t.strip().upper() for t in only.split(",")} if only else None
        self.max_jobs = int(max_jobs)      # jobs per company (API platforms)
        self.max_pages = int(max_pages)    # pages per company (custom sites)
        self.counts = defaultdict(int)

    # ------------------------------------------------------------------
    # Start: read the CSV and route each company by platform.
    # ------------------------------------------------------------------
    async def start(self):
        if not CSV_FILE.exists():
            self.logger.error("CSV file not found: %s", CSV_FILE)
            return

        with open(CSV_FILE, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))

        for row in rows:
            ticker = (row.get("ticker") or "").strip().upper()
            if self.only and ticker not in self.only:
                continue

            url = (
                (row.get("job_listing_url") or "").strip()
                or (row.get("career_site_url") or "").strip()
            )
            if not url.startswith(("http://", "https://")):
                self.logger.warning("Skipping invalid URL for %s: %s", ticker, url)
                continue

            platform = detect_platform(url)
            kw = {"row": row, "platform": platform}

            if platform == "Workday":
                api = workday_api_base(url)
                if api:
                    yield self.workday_page(api, 0, 0, **kw)
                    continue

            if platform == "Greenhouse":
                board = greenhouse_board(url)
                if board:
                    yield scrapy.Request(
                        f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true",
                        callback=self.parse_greenhouse,
                        errback=self.on_error, cb_kwargs=kw,
                    )
                    continue

            if platform == "Lever":
                company = lever_company(url)
                if company:
                    yield scrapy.Request(
                        f"https://api.lever.co/v0/postings/{company}?mode=json",
                        callback=self.parse_lever,
                        errback=self.on_error, cb_kwargs=kw,
                    )
                    continue

            # Everything else: crawl the HTML page(s).
            yield scrapy.Request(
                url,
                callback=self.parse_page,
                errback=self.on_error,
                meta=self.render_meta(),
                cb_kwargs={**kw, "host": urlparse(url).netloc.lower(), "depth": 0},
            )

    # ------------------------------------------------------------------
    # Workday (JSON API)
    # ------------------------------------------------------------------
    def workday_page(self, api, offset, total, row, platform):
        body = {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""}
        return scrapy.Request(
            f"{api}/jobs",
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            body=json.dumps(body),
            callback=self.parse_workday_list,
            errback=self.on_error,
            cb_kwargs={"api": api, "offset": offset, "total": total,
                       "row": row, "platform": platform},
        )

    def parse_workday_list(self, response, api, offset, total, row, platform):
        data = response.json()
        # Workday only reports "total" on the first page, so remember it.
        total = data.get("total") or total
        key = self.key(row)

        for posting in data.get("jobPostings", []):
            if self.counts[key] >= self.max_jobs:
                return
            self.counts[key] += 1
            yield scrapy.Request(
                f"{api}{posting['externalPath']}",
                headers={"Accept": "application/json"},
                callback=self.parse_workday_job,
                errback=self.on_error,
                cb_kwargs={"row": row, "platform": platform},
            )

        if offset + 20 < total and self.counts[key] < self.max_jobs:
            yield self.workday_page(api, offset + 20, total, row, platform)

    def parse_workday_job(self, response, row, platform):
        info = response.json().get("jobPostingInfo", {})
        yield self.make_item(
            row, platform,
            url=info.get("externalUrl") or response.url,
            title=info.get("title", ""),
            text=html_to_text(info.get("jobDescription", "")),
            location=info.get("location", ""),
        )

    # ------------------------------------------------------------------
    # Greenhouse (JSON API)
    # ------------------------------------------------------------------
    def parse_greenhouse(self, response, row, platform):
        for job in response.json().get("jobs", [])[: self.max_jobs]:
            yield self.make_item(
                row, platform,
                url=job.get("absolute_url", response.url),
                title=job.get("title", ""),
                text=html_to_text(job.get("content", "")),
                location=(job.get("location") or {}).get("name", ""),
            )

    # ------------------------------------------------------------------
    # Lever (JSON API)
    # ------------------------------------------------------------------
    def parse_lever(self, response, row, platform):
        for job in response.json()[: self.max_jobs]:
            parts = [job.get("descriptionPlain", "")]
            for section in job.get("lists", []):
                parts.append(section.get("text", ""))
                parts.append(html_to_text(section.get("content", "")))
            parts.append(job.get("additionalPlain", ""))
            yield self.make_item(
                row, platform,
                url=job.get("hostedUrl", response.url),
                title=job.get("text", ""),
                text="\n".join(p for p in parts if p),
                location=(job.get("categories") or {}).get("location", ""),
            )

    # ------------------------------------------------------------------
    # Custom sites (HTML, optionally rendered by Playwright)
    # ------------------------------------------------------------------
    def parse_page(self, response, row, platform, host, depth):
        content_type = response.headers.get("Content-Type", b"").decode(
            "utf-8", errors="ignore").lower()
        if content_type and "text/html" not in content_type:
            return

        key = self.key(row)
        self.counts[key] += 1

        title = response.css("title::text").get(default="").strip()
        headings = [
            t.strip() for t in response.css("h1::text, h2::text, h3::text").getall()
            if t.strip()
        ]
        body_text = page_text(response)

        page_text = (response.url + " " + title + " " + " ".join(headings)
                     + " " + body_text).lower()
        if any(sig in page_text for sig in JOB_SIGNALS):
            item = self.make_item(row, platform, response.url, title, body_text)
            item["record_type"] = "page"      # a page, not a single job
            item["headings"] = headings
            yield item

        if depth >= 2 or self.counts[key] >= self.max_pages:
            return

        for link in response.css("a::attr(href)").getall():
            next_url = response.urljoin(link).split("#")[0]
            parsed = urlparse(next_url)
            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.netloc.lower() != host:
                continue
            if parsed.path.lower().endswith(SKIP_EXT):
                continue
            if not any(h in next_url.lower() for h in LINK_HINTS):
                continue
            yield scrapy.Request(
                next_url,
                callback=self.parse_page,
                errback=self.on_error,
                meta=self.render_meta(),
                cb_kwargs={"row": row, "platform": platform,
                           "host": host, "depth": depth + 1},
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def render_meta(self):
        if not HAVE_PLAYWRIGHT:
            return {}
        return {
            "playwright": True,
            "playwright_page_goto_kwargs": {"wait_until": "domcontentloaded"},
            "playwright_page_methods": [PageMethod("wait_for_timeout", 3000)],
        }

    @staticmethod
    def key(row):
        return (row.get("ticker") or row.get("company_name") or "").strip()

    def make_item(self, row, platform, url, title, text, location=""):
        a = analyze_job(title, text)
        return {
            "record_type": "job",
            "company_name": row.get("company_name", ""),
            "ticker": row.get("ticker", ""),
            "sector": row.get("sector", ""),
            "domain": urlparse(url).netloc,
            "job_platform": platform,
            "source_url": url,
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
            "description": text[:5000],
            "crawl_date": now_iso(),
        }

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
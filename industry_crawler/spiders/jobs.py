import json
import re
from importlib.util import find_spec
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit

import scrapy

from ..company_input import read_companies
from ..normalization import canonical_url
from .skill_categories import analyze_job
from .platforms import (
    detect_platform, workday_api_base, workday_search_filters, greenhouse_board,
    lever_company, html_to_text,
)

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

    Workday / Greenhouse / Lever use JSON APIs without a browser.
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

            self.source_summary["scheduled"] += 1
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
                        f"https://api.lever.co/v0/postings/{company}?mode=json&skip=0&limit=100",
                        callback=self.parse_lever,
                        errback=self.on_error, cb_kwargs=kw,
                    )
                    continue

            # Everything else: crawl the HTML page(s).
            self.pages[self.key(row)] = 1
            self.seen_pages[self.key(row)].add(canonical_url(url))
            yield scrapy.Request(
                url,
                callback=self.parse_page,
                errback=self.on_error,
                meta=self.render_meta(),
                cb_kwargs={**kw, "host": urlparse(url).netloc.lower(), "depth": 0},
            )

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

    def workday_page(self, api, offset, total, row, platform):
        facets, search = workday_search_filters(row.get("job_listing_url", ""))
        body = {"appliedFacets": facets, "limit": 20, "offset": offset, "searchText": search}
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

        postings = data.get("jobPostings", [])
        for posting in postings:
            if self.counts[key] >= self.max_jobs:
                return
            self.counts[key] += 1
            yield scrapy.Request(
                f"{api}{posting['externalPath']}",
                headers={"Accept": "application/json"},
                callback=self.parse_workday_job,
                errback=self.on_error,
                cb_kwargs={"row": row, "platform": platform, "external_path": posting["externalPath"]},
            )

        if postings and offset + 20 < total and self.counts[key] < self.max_jobs:
            yield self.workday_page(api, offset + 20, total, row, platform)

    def parse_workday_job(self, response, row, platform, external_path=""):
        info = response.json().get("jobPostingInfo", {})
        board = urlsplit(row.get("job_listing_url", ""))
        # Search filters belong to the board, not to the individual posting path.
        board_url = urlunsplit((board.scheme, board.netloc, board.path.rstrip("/"), "", ""))
        yield self.make_item(
            row, platform,
            url=info.get("externalUrl") or board_url + external_path,
            job_id=info.get("jobReqId", ""),
            employment_type=info.get("timeType", ""),
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
                url=job.get("absolute_url", ""),
                job_id=job.get("id", ""),
                title=job.get("title", ""),
                text=html_to_text(job.get("content", "")),
                location=(job.get("location") or {}).get("name", ""),
            )

    # ------------------------------------------------------------------
    # Lever (JSON API)
    # ------------------------------------------------------------------
    def parse_lever(self, response, row, platform):
        jobs = response.json()
        key = self.key(row)
        for job in jobs:
            if self.counts[key] >= self.max_jobs:
                return
            self.counts[key] += 1
            parts = [job.get("descriptionPlain", "")]
            for section in job.get("lists", []):
                parts.append(section.get("text", ""))
                parts.append(html_to_text(section.get("content", "")))
            parts.append(job.get("additionalPlain", ""))
            yield self.make_item(
                row, platform,
                url=job.get("hostedUrl", ""),
                job_id=job.get("id", ""),
                employment_type=(job.get("categories") or {}).get("commitment", ""),
                title=job.get("text", ""),
                text="\n".join(p for p in parts if p),
                location=(job.get("categories") or {}).get("location", ""),
            )

        if len(jobs) == 100 and self.counts[key] < self.max_jobs:
            from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
            parts = urlsplit(response.url)
            query = parse_qs(parts.query)
            query["skip"] = [str(int(query.get("skip", ["0"])[0]) + len(jobs))]
            yield scrapy.Request(urlunsplit(parts._replace(query=urlencode(query, doseq=True))),
                                 callback=self.parse_lever, errback=self.on_error,
                                 cb_kwargs={"row": row, "platform": platform})

    # ------------------------------------------------------------------
    # Custom sites (HTML, optionally rendered by Playwright)
    # ------------------------------------------------------------------
    def parse_page(self, response, row, platform, host, depth):
        content_type = response.headers.get("Content-Type", b"").decode().lower()
        if content_type and "html" not in content_type:
            return
        key = self.key(row)
        body_text = html_to_text(response.css("main, article").get() or response.css("body").get() or "")
        title = response.css("h1::text").get(default="").strip()
        postings = []
        def visit(node):
            if isinstance(node, list):
                for child in node:
                    visit(child)
            elif isinstance(node, dict):
                types = node.get("@type", [])
                if "JobPosting" in ([types] if isinstance(types, str) else types):
                    postings.append(node)
                for value in node.values():
                    if isinstance(value, (dict, list)):
                        visit(value)
        for raw in response.css('script[type="application/ld+json"]::text').getall():
            try:
                visit(json.loads(raw))
            except (ValueError, TypeError):
                self.logger.warning("Invalid structured data at %s", response.url)
        for job in postings:
            if self.counts[key] >= self.max_jobs:
                break
            self.counts[key] += 1
            identifier = job.get("identifier", "")
            if isinstance(identifier, dict):
                identifier = identifier.get("value", "")
            locations = job.get("jobLocation", [])
            if isinstance(locations, dict):
                locations = [locations]
            location = "; ".join(
                ", ".join(str(v) for k, v in loc.get("address", {}).items() if k != "@type")
                for loc in locations if isinstance(loc, dict) and isinstance(loc.get("address"), dict))
            yield self.make_item(row, platform, response.urljoin(job.get("url") or response.url),
                                 job.get("title", ""), html_to_text(job.get("description", "")),
                                 location, identifier, job.get("employmentType", ""))
        if not postings:
            requisition = re.search(r"(?:requisition|job)\s*(?:id|number|#)\s*:?\s*([\w-]+)", body_text, re.I)
            specific_url = re.search(r"/(?:job|jobs|position|positions)/[^/]+", response.url)
            sections = analyze_job(title, body_text)
            if (title and len(body_text) > 150 and (requisition or specific_url)
                    and sections["responsibilities"] and sections["requirements"]):
                if self.counts[key] < self.max_jobs:
                    self.counts[key] += 1
                    yield self.make_item(row, platform, response.url, title, body_text,
                                         job_id=requisition.group(1) if requisition else "")
            else:
                yield {"record_type": "job_board" if any(h in response.url.lower() for h in LINK_HINTS)
                       else "non_job_page", **row, "source_url": response.url}
        if depth >= 3 or self.pages[key] >= self.max_pages or self.counts[key] >= self.max_jobs:
            return
        for link in response.css("a::attr(href)").getall():
            next_url = canonical_url(response.urljoin(link))
            parsed = urlparse(next_url)
            if (parsed.netloc.lower() != host or parsed.path.lower().endswith(SKIP_EXT)
                    or not any(h in next_url.lower() for h in LINK_HINTS)):
                continue
            if next_url in self.seen_pages[key]:
                continue
            # Bound scheduled pages, rather than only completed responses.
            if self.pages[key] >= self.max_pages:
                break
            self.pages[key] += 1
            self.seen_pages[key].add(next_url)
            yield scrapy.Request(next_url, callback=self.parse_page, errback=self.on_error,
                                 meta=self.render_meta(), cb_kwargs={"row": row, "platform": platform,
                                 "host": host, "depth": depth + 1})

    def render_meta(self):
        if not self.render:
            return {}
        return {"playwright": True,
                "playwright_page_goto_kwargs": {"wait_until": "domcontentloaded"}}

    @staticmethod
    def key(row):
        return (row.get("ticker") or row.get("company_name") or "").strip()

    def make_item(self, row, platform, url, title, text, location="", job_id="", employment_type=""):
        a = analyze_job(title, text)
        return {
            "record_type": "job",
            "company_name": row.get("company_name", ""),
            "ticker": row.get("ticker", ""),
            "sector": row.get("sector", ""),
            "domain": urlparse(url).netloc,
            "job_platform": platform,
            "source_url": url,
            "job_url": canonical_url(url),
            "job_board_url": row.get("job_listing_url", ""),
            "job_id": str(job_id or ""),
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

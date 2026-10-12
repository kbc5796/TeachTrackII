import csv
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import scrapy

try:
    from .skill_categories import analyze_job
    from .platforms import page_text
except ImportError:                      # if the files sit outside a package
    from skill_categories import analyze_job
    from platforms import page_text


# company_sources.csv lives in the outer project folder (same file the
# jobs spider reads), so both crawlers work from the same company list.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CSV_FILE = PROJECT_ROOT / "company_sources.csv"


# Words that can indicate useful industry information (research doc, section 8).
# CORE words are about people, skills and work, which is what TeachTrack needs,
# so they count anywhere on the page. GENERIC words appear on almost every
# marketing page, so they only count in the URL, title and headings.
CORE_KEYWORDS = [
    "career", "careers", "job", "jobs", "skills", "occupation",
    "occupations", "engineering", "developer", "requirements",
    "workforce", "industries",
]
GENERIC_KEYWORDS = [
    "software", "technology", "technologies", "industry", "development",
    "education", "tools", "products", "services",
]
KEYWORDS = CORE_KEYWORDS + GENERIC_KEYWORDS

# Pages that never hold useful industry information.
SKIP_URL_HINTS = [
    "login", "signin", "sign-in", "privacy", "cookie", "terms-of",
    "buy", "pricing", "shop", "store", "download", "cart", "checkout",
    "gaming",
]
SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".mp4", ".mp3",
            ".svg", ".css", ".js", ".xml", ".gz")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def site_url(row):
    """The company's main website, where the industry crawl starts.
    Uses the website_url column if filled in; otherwise guesses it from the
    career site (careers.microsoft.com -> www.microsoft.com)."""
    explicit = (row.get("website_url") or "").strip()
    if explicit.startswith(("http://", "https://")):
        return explicit

    career = (row.get("career_site_url") or row.get("job_listing_url") or "").strip()
    if not career.startswith(("http://", "https://")):
        return ""
    p = urlparse(career)
    host = p.netloc.lower()
    label, _, rest = host.partition(".")
    if label in {"careers", "career", "jobs", "apply"} and rest:
        host = rest if rest.startswith("www.") else "www." + rest
    return f"{p.scheme}://{host}/"


def url_score(url):
    """How promising a URL looks, before we download it. -1 = skip."""
    low = url.lower()
    path = urlparse(low).path
    if path.endswith(SKIP_EXT) or any(h in low for h in SKIP_URL_HINTS):
        return -1
    return sum(1 for k in KEYWORDS if k in low)


class IndustrySpider(scrapy.Spider):
    """
    Industry crawler. Reads the same company_sources.csv as the jobs spider
    and writes records with the same company fields (company_name, ticker,
    sector, domain, source_url, crawl_date) and the same analysis fields
    (categories, skills, technologies, education, AI flag).

    Per company: start at the main website, use the sitemap when there is
    one, otherwise follow links, score each page for relevance, and keep
    pages that score high enough.

    Test small first:
        scrapy crawl industry -a only=NVDA,MSFT -a max_pages=15 -O industry_test.json
    Full run:
        scrapy crawl industry -O industry_data.json
    """
    name = "industry"

    custom_settings = {
        # --- responsible crawling (research doc, sections 6 and 7) ---
        "ROBOTSTXT_OBEY": True,
        "DOWNLOAD_DELAY": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 1,
        "AUTOTHROTTLE_MAX_DELAY": 5,
        "USER_AGENT": "TeachTrackBot (university research; contact: YOUR_EMAIL_HERE)",
        # Limits are per company (max_pages), not a global page count.
        "DEPTH_LIMIT": 0,
        "CLOSESPIDER_PAGECOUNT": 0,
    }

    def __init__(self, only=None, max_pages=30, min_score=4, sitemap=1,
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        # only="MSFT,NVDA" limits the run to those tickers (for testing).
        self.only = {t.strip().upper() for t in only.split(",")} if only else None
        self.max_pages = int(max_pages)    # pages requested per company
        self.min_score = int(min_score)    # relevance needed to save a page
        self.use_sitemap = str(sitemap) not in ("0", "false", "False")
        self.scheduled = defaultdict(int)  # pages requested per company

    # ------------------------------------------------------------------
    # Start: one homepage request (and one sitemap request) per company.
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

            url = site_url(row)
            if not url:
                self.logger.warning("No usable website for %s", ticker)
                continue

            parsed = urlparse(url)
            host = parsed.netloc.lower()
            origin = f"{parsed.scheme}://{parsed.netloc}"
            key = self.key(row)

            self.scheduled[key] += 1
            yield scrapy.Request(
                url, callback=self.parse_page, errback=self.on_error,
                cb_kwargs={"row": row, "host": host, "depth": 0},
            )

            if self.use_sitemap:
                yield scrapy.Request(
                    f"{origin}/sitemap.xml",
                    callback=self.parse_sitemap,
                    errback=self.on_sitemap_error,
                    cb_kwargs={"row": row, "host": host, "nested": 0},
                )

    # ------------------------------------------------------------------
    # Sitemap: rank the listed URLs and fetch the most promising ones.
    # ------------------------------------------------------------------
    def parse_sitemap(self, response, row, host, nested):
        locs = [u.strip() for u in
                response.xpath("//*[local-name()='loc']/text()").getall()]
        if not locs:
            return

        key = self.key(row)
        pages, sitemaps = [], []
        for loc in locs:
            if urlparse(loc).netloc.lower() != host:
                continue
            if loc.lower().endswith(".gz"):
                continue
            if loc.lower().endswith(".xml"):
                sitemaps.append(loc)
            else:
                score = url_score(loc)
                if score > 0:
                    pages.append((score, loc))

        # A sitemap index points to more sitemaps; look one level down.
        if nested == 0:
            for sm in sorted(sitemaps, key=url_score, reverse=True)[:3]:
                yield scrapy.Request(
                    sm, callback=self.parse_sitemap, errback=self.on_sitemap_error,
                    cb_kwargs={"row": row, "host": host, "nested": 1},
                )

        for score, loc in sorted(pages, reverse=True):
            if self.scheduled[key] >= self.max_pages:
                break
            self.scheduled[key] += 1
            yield scrapy.Request(
                loc, callback=self.parse_page, errback=self.on_error,
                priority=score,
                cb_kwargs={"row": row, "host": host, "depth": 1},
            )

    # ------------------------------------------------------------------
    # Page: score it, save it if relevant, follow the best links.
    # ------------------------------------------------------------------
    def parse_page(self, response, row, host, depth):
        content_type = response.headers.get("Content-Type", b"").decode(
            "utf-8", errors="ignore").lower()
        if content_type and "text/html" not in content_type:
            return

        title = response.css("title::text").get(default="").strip()
        headings = [
            t.strip() for t in response.css("h1::text, h2::text, h3::text").getall()
            if t.strip()
        ]
        description = response.css(
            'meta[name="description"]::attr(content)').get(default="").strip()
        body_text = page_text(response)

        score, matched = self.relevance(response.url, title, headings, body_text)

        if score >= self.min_score:
            yield self.make_item(row, response.url, title, description,
                                 headings, body_text, score, matched)

        if depth >= 2:
            return

        key = self.key(row)
        candidates = {}
        for link in response.css("a::attr(href)").getall():
            next_url = response.urljoin(link).split("#")[0]
            parsed = urlparse(next_url)
            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.netloc.lower() != host:
                continue
            s = url_score(next_url)
            if s < 0:
                continue
            candidates[next_url] = s

        # Best-looking links first, until this company's page budget is used.
        for next_url, s in sorted(candidates.items(), key=lambda x: -x[1]):
            if self.scheduled[key] >= self.max_pages:
                break
            self.scheduled[key] += 1
            yield scrapy.Request(
                next_url, callback=self.parse_page, errback=self.on_error,
                priority=s,
                cb_kwargs={"row": row, "host": host, "depth": depth + 1},
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def key(row):
        return (row.get("ticker") or row.get("company_name") or "").strip()

    @staticmethod
    def relevance(url, title, headings, body_text):
        """URL + title + heading + text relevance (research doc, section 8)."""
        url_l, title_l = url.lower(), title.lower()
        head_l, body_l = " ".join(headings).lower(), body_text.lower()
        score, matched = 0, set()
        for kw in CORE_KEYWORDS:
            if kw in url_l:
                score += 3
            elif kw in title_l:
                score += 2
            elif kw in head_l or kw in body_l:
                score += 1
            else:
                continue
            matched.add(kw)
        for kw in GENERIC_KEYWORDS:
            if kw in url_l or kw in title_l or kw in head_l:
                score += 1
                matched.add(kw)
        return score, sorted(matched)

    def make_item(self, row, url, title, description, headings, text,
                  score, matched):
        a = analyze_job(title, text)
        return {
            # --- same company fields as the jobs spider ---
            "record_type": "industry_page",
            "company_name": row.get("company_name", ""),
            "ticker": row.get("ticker", ""),
            "sector": row.get("sector", ""),
            "domain": urlparse(url).netloc,
            "source_url": url,
            "crawl_date": now_iso(),
            # --- same analysis fields as the jobs spider ---
            "primary_category": a["primary_category"],
            "categories": a["categories"],
            "is_ai_related": a["is_ai_job"],
            "ai_signal": a["ai_signal"],
            "skills": a["skills"],
            "technologies": a["technologies"],
            "education": a["education"],
            # --- industry-page-specific fields ---
            "page_title": title,
            "description": description,
            "headings": headings,
            "relevance_score": score,
            "matched_keywords": matched,
            "text": text[:5000],
        }

    def on_sitemap_error(self, failure):
        # A missing sitemap is normal; we just fall back to following links.
        self.logger.debug("No sitemap at %s", failure.request.url)

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
            "reason": failure.value.__class__.__name__,
            "detail": str(failure.value)[:200],
            "crawl_date": now_iso(),
        }
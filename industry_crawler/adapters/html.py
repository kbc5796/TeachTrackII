import json
import re
from urllib.parse import urlparse
import scrapy
from ..normalization import canonical_url
from ..spiders.platforms import html_to_text
from ..spiders.skill_categories import analyze_job

from .base import JobAdapter

LINK_HINTS = ["job", "career", "position", "opening", "requisition", "search"]
SKIP_EXT = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".mp4", ".mp3")


class HTMLAdapter(JobAdapter):
    def start(self, row, url, platform):
        self.pages[self.key(row)] = 1
        self.seen_pages[self.key(row)].add(canonical_url(url))
        return scrapy.Request(url, callback=self.spider.parse_page, errback=self.on_error,
                              meta=self.render_meta(), cb_kwargs={"row": row, "platform": platform,
                              "host": urlparse(url).netloc.lower(), "depth": 0})

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
            yield scrapy.Request(next_url, callback=self.spider.parse_page, errback=self.on_error,
                                 meta=self.render_meta(), cb_kwargs={"row": row, "platform": platform,
                                 "host": host, "depth": depth + 1})

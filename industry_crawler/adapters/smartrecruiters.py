"""Public employer Posting API; never follow response-provided API refs."""
from urllib.parse import quote, urlsplit

import scrapy

from .base import JobAdapter
from ..spiders.platforms import html_to_text
from ..normalization import clean_text


class SmartRecruitersAdapter(JobAdapter):
    def start(self, row, url, platform):
        parts = [p for p in urlsplit(url).path.split("/") if p]
        if not parts:
            return None
        company = parts[0]
        return self.page(row, platform, company, 0)

    def request(self, url, row, platform, action, **kwargs):
        return scrapy.Request(url, callback=self.spider.parse_api, errback=self.on_error,
                              cb_kwargs={"row": row, "platform": platform, "action": action, **kwargs})

    def page(self, row, platform, company, offset):
        base = f"https://api.smartrecruiters.com/v1/companies/{quote(company, safe='')}/postings"
        return self.request(f"{base}?limit=100&offset={offset}", row, platform, "parse_list",
                            company=company, offset=offset)

    def parse_list(self, response, row, platform, company, offset):
        data = self.data(response)
        jobs = self.records(data, "content")
        total = int(data.get("totalFound", 0))
        limit = int(data.get("limit", 100))
        if limit <= 0 or total < 0:
            raise ValueError("Invalid pagination")
        key = self.key(row)
        for job in jobs:
            if self.counts[key] >= self.max_jobs:
                return
            self.counts[key] += 1
            if not isinstance(job, dict) or not isinstance(job.get("id"), (str, int)) or not job["id"]:
                yield self.malformed(response, row)
                continue
            base = f"https://api.smartrecruiters.com/v1/companies/{quote(company, safe='')}/postings"
            yield self.request(f"{base}/{quote(str(job['id']), safe='')}", row, platform, "parse_job")
        if jobs and offset + limit < total and self.counts[key] < self.max_jobs:
            yield self.page(row, platform, company, offset + limit)

    def parse_job(self, response, row, platform):
        job = self.data(response)
        if job.get("active") is False:
            yield {**self.malformed(response, row), "reason": "inactive posting"}
            return
        sections = self.mapping(self.mapping(job.get("jobAd")).get("sections"))
        description = "\n".join(
            html_to_text(section.get("title", "")) + "\n" + html_to_text(section.get("text", ""))
            for section in sections.values() if isinstance(section, dict) and section.get("text"))
        loc = self.mapping(job.get("location"))
        location = ", ".join(str(loc[k]) for k in ("city", "region", "country") if loc.get(k))
        if loc.get("remote"):
            location = "; ".join(filter(None, [location, "Remote"]))
        item = self.make_item(row, platform, url=job.get("postingUrl", ""), title=job.get("name", ""),
                              text=description, location=location, job_id=job.get("id", ""),
                              employment_type=self.mapping(job.get("typeOfEmployment")).get("label", ""))
        item["requisition_id"] = clean_text(job.get("refNumber"))
        item["source_employer"] = job.get("company") or {}
        yield item

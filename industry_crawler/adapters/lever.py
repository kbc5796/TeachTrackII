import scrapy
from ..spiders.platforms import lever_company, html_to_text
from .base import JobAdapter


class LeverAdapter(JobAdapter):
    def start(self, row, url, platform):
        company = lever_company(url)
        if company:
            return scrapy.Request(f"https://api.lever.co/v0/postings/{company}?mode=json&skip=0&limit=100",
                                  callback=self.spider.parse_lever, errback=self.on_error,
                                  cb_kwargs={"row": row, "platform": platform})

    def parse_lever(self, response, row, platform):
        jobs = self.data(response, list)
        key = self.key(row)
        for job in jobs:
            if self.counts[key] >= self.max_jobs:
                return
            self.counts[key] += 1
            if not isinstance(job, dict):
                yield self.malformed(response, row)
                continue
            parts = [job.get("descriptionPlain", "")]
            for section in job.get("lists") or []:
                if not isinstance(section, dict):
                    continue
                parts.append(section.get("text", ""))
                parts.append(html_to_text(section.get("content", "")))
            parts.append(job.get("additionalPlain", ""))
            yield self.make_item(
                row, platform,
                url=job.get("hostedUrl", ""),
                job_id=job.get("id", ""),
                employment_type=self.mapping(job.get("categories")).get("commitment", ""),
                title=job.get("text", ""),
                text="\n".join(p for p in parts if isinstance(p, str) and p),
                location=self.mapping(job.get("categories")).get("location", ""),
            )

        if len(jobs) == 100 and self.counts[key] < self.max_jobs:
            from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
            parts = urlsplit(response.url)
            query = parse_qs(parts.query)
            query["skip"] = [str(int(query.get("skip", ["0"])[0]) + len(jobs))]
            yield scrapy.Request(urlunsplit(parts._replace(query=urlencode(query, doseq=True))),
                                 callback=self.spider.parse_lever, errback=self.on_error,
                                 cb_kwargs={"row": row, "platform": platform})

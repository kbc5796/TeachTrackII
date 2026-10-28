import scrapy
from ..spiders.platforms import greenhouse_board, html_to_text
from .base import JobAdapter
from ..normalization import clean_text


class GreenhouseAdapter(JobAdapter):
    def start(self, row, url, platform):
        board = greenhouse_board(url)
        if board:
            return scrapy.Request(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true",
                                  callback=self.spider.parse_greenhouse, errback=self.on_error,
                                  cb_kwargs={"row": row, "platform": platform})

    def parse_greenhouse(self, response, row, platform):
        for job in self.records(self.data(response), "jobs")[: self.max_jobs]:
            if not isinstance(job, dict):
                yield self.malformed(response, row)
                continue
            item = self.make_item(
                row, platform,
                url=job.get("absolute_url", ""),
                job_id=job.get("id", ""),
                title=job.get("title", ""),
                text=html_to_text(job.get("content", "")),
                location=self.mapping(job.get("location")).get("name", ""),
            )
            item["requisition_id"] = clean_text(job.get("requisition_id"))
            yield item

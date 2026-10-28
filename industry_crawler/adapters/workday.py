import json
from urllib.parse import urlsplit, urlunsplit
import scrapy
from ..spiders.platforms import workday_api_base, workday_search_filters, html_to_text
from .base import JobAdapter


class WorkdayAdapter(JobAdapter):
    def start(self, row, url, platform):
        api = workday_api_base(url)
        return self.workday_page(api, 0, 0, row, platform) if api else None

    def workday_page(self, api, offset, total, row, platform):
        facets, search = workday_search_filters(row.get("job_listing_url", ""))
        body = {"appliedFacets": facets, "limit": 20, "offset": offset, "searchText": search}
        return scrapy.Request(
            f"{api}/jobs",
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            body=json.dumps(body),
            callback=self.spider.parse_workday_list,
            errback=self.on_error,
            cb_kwargs={"api": api, "offset": offset, "total": total,
                       "row": row, "platform": platform},
        )

    def parse_workday_list(self, response, api, offset, total, row, platform):
        data = self.data(response)
        # Workday only reports "total" on the first page, so remember it.
        total = int(data.get("total") or total)
        if total < 0:
            raise ValueError("Invalid Workday total")
        key = self.key(row)

        postings = self.records(data, "jobPostings")
        for posting in postings:
            if self.counts[key] >= self.max_jobs:
                return
            self.counts[key] += 1
            if (not isinstance(posting, dict) or not isinstance(posting.get("externalPath"), str)
                    or not posting["externalPath"].startswith("/job/")):
                yield self.malformed(response, row)
                continue
            yield scrapy.Request(
                f"{api}{posting['externalPath']}",
                headers={"Accept": "application/json"},
                callback=self.spider.parse_workday_job,
                errback=self.on_error,
                cb_kwargs={"row": row, "platform": platform, "external_path": posting["externalPath"]},
            )

        if postings and offset + 20 < total and self.counts[key] < self.max_jobs:
            yield self.workday_page(api, offset + 20, total, row, platform)

    def parse_workday_job(self, response, row, platform, external_path=""):
        info = self.data(response).get("jobPostingInfo", {})
        if not isinstance(info, dict):
            yield self.malformed(response, row)
            return
        board = urlsplit(row.get("job_listing_url", ""))
        board_url = urlunsplit((board.scheme, board.netloc, board.path.rstrip("/"), "", ""))
        item = self.make_item(
            row, platform,
            url=info.get("externalUrl") or board_url + external_path,
            job_id=info.get("jobReqId", ""),
            employment_type=info.get("timeType", ""),
            title=info.get("title", ""),
            text=html_to_text(info.get("jobDescription", "")),
            location=info.get("location", ""),
        )

        item["requisition_id"] = item["job_id"]
        yield item

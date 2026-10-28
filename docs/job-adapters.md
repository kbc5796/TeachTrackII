# Contributing a job adapter or source

Collection supports all eligible companies in the existing 500-company S&P 500
dataset. The top 100 are prioritized only for manual verification of listing URLs,
API functionality and United States location filters; they are not a crawl
restriction. There is no top-100-only allowlist.

Use the job-source proposal issue template to suggest public job API endpoints or
documentation, direct employer listing URLs, corrections to existing links,
better United States filters, or new companies for possible future expansion.
Only a company name and suggested URL are required; explanation is optional.
Maintainers manually review every suggestion. An issue never automatically adds
a company or URL to the crawler and never triggers a crawl. Companies outside the
current S&P 500 dataset remain inactive unless maintainers explicitly expand the
approved company scope through a reviewed repository change.

`company_sources.csv` remains the existing 500-company source dataset and approved
source allowlist. `sources=...` can select existing ticker/URL pairs, including
their current query filters, but cannot authorize new targets. Invalid/excluded
repository sources remain blocked. Suggestions do not bypass approved-source,
request-host, redirect, robots or access restrictions. No discovery service,
submission backend or automatic approval is provided. Changes to provider API
hosts require a code review of `source_policy.py`.

Maintainers must follow the [manual source verification guide](manual-sources.md)
to establish employer ownership, test the actual API requests and returned job
locations, and record whether filters really limit results to United States jobs
before approving a source correction. Do not approve aggregators, credentials,
private hosts or unsupported endpoints. Approval requires an explicit reviewed
repository change; an issue submission alone is never approval.

## Adapter contract

1. Add a small module under `industry_crawler/adapters/` implementing
   `JobAdapter.start(row, url, platform)`. Return a Scrapy request or `None` to use
   the HTML fallback. Use the approved row for employer identity; preserve vendor
   employer metadata separately when useful.
2. Add the class to `ADAPTERS` and, for a new provider host, extend the public
   `detect_platform` helper and `API_PLATFORMS`. No source-routing change in the
   central spider is required.
3. Use `spider.parse_api` with an `action` of `parse_list` or `parse_job` as the
   request callback. This named spider method survives Scrapy JOBDIR serialization.
   Existing Workday/Greenhouse/Lever callback wrappers remain for compatibility.
4. Return jobs through `self.make_item(...)`: required employer name (row), title,
   full description and HTTP(S) individual posting URL. Supply platform posting ID,
   location and employment type when available. Missing values remain empty.
   Never synthesize requisition IDs; only put an explicit employer reference into
   `requisition_id`. Do not confuse board URLs or internal API URLs with posting URLs.
   `items.JobRecord` documents standardized fields; the pipeline validates and
   filters jobs using the existing analyzer.
5. Use `self.data`/`self.records` for JSON/container checks. Quarantine malformed
   individual entries using `self.malformed`, and continue with valid siblings.
   Use `errback=self.on_error`; never disable robots, retries, delays or throttling.
   Check `self.counts[self.key(row)]` against `max_jobs` before scheduling/examining
   each job. Increment pagination only according to documented API semantics.
6. Mock list/detail, pagination, invalid JSON, malformed fields, API errors, empty
   lists and limits. Test callback serialization and source restrictions. The main
   suite must not make live network calls.

## Minimal example (template, not a registered live endpoint)

```python
from scrapy import Request
from .base import JobAdapter

class ExampleAdapter(JobAdapter):
    def start(self, row, url, platform):
        # Use only a documented, reviewed employer endpoint here.
        return Request(url, callback=self.spider.parse_api, errback=self.on_error,
                       cb_kwargs={"row": row, "platform": platform, "action": "parse_list"})

    def parse_list(self, response, row, platform):
        for job in self.records(self.data(response), "jobs"):
            key = self.key(row)
            if self.counts[key] >= self.max_jobs:
                return
            self.counts[key] += 1
            if not isinstance(job, dict):
                yield self.malformed(response, row)
                continue
            yield self.make_item(row, platform, url=job.get("url", ""),
                                 title=job.get("title", ""), text=job.get("description", ""),
                                 job_id=job.get("id", ""))
```

Use `tests/test_adapters_reliability.py` as an offline testing template and
`adapters/smartrecruiters.py` as the complete list/detail/pagination example.
Run `python -m pytest -q`, `python -m ruff check .` and `git diff --check`.

## Reviewing duplicate candidates

Inspect `duplicate_review` identity keys in SQLite alongside `source_observations`.
Verify the same employer and explicit source IDs/references before reconciling
anything. Identical titles, locations or descriptions are insufficient. This
change flags candidates but performs no historical deletion or bulk merge.

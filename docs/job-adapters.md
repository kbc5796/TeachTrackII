# Contributing a job adapter or source

Collection is limited to the existing S&P 500 company dataset. Open a GitHub issue
using the job-source proposal template, or submit a PR documenting a corrected
source for an existing ticker. Include the official employer careers page, exact
listing URL, public API documentation if applicable, location/search filters,
robots/access limitations and a small offline fixture. Maintainers must manually
verify company ownership, endpoint purpose, safe HTTPS hosts and the existing
company's membership before merging. A proposal or issue never triggers a crawl.
Do not submit aggregators, credentials, private hosts or new companies.

`company_sources.csv` is the reviewed repository allowlist. `sources=...` can
select existing ticker/URL pairs, including their current query filters, but
cannot authorize new targets. Invalid/excluded repository sources are blocked.
No discovery service, submission endpoint or automatic approval is provided.
Changes to provider API hosts require a code review of `source_policy.py`.

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

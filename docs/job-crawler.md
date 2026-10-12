# CS job crawler

## Audit and scope

The original jobs spider already had useful Workday list/detail requests,
Greenhouse content extraction, Lever sections, company metadata, and per-company
limits. The keyword analyzer already used token boundaries, education patterns,
section headings, and five categories. Those interfaces remain in use.

The original custom parser shadowed `page_text`, accepted generic careers content,
launched rendering on every custom request when installed, and used a fixed delay.
Descriptions were truncated, IDs and employment types were missing, and the
pipeline did not validate, filter, deduplicate, or persist records. Existing JSON
outputs therefore are historical mixed records, not validated current CS datasets.
The industry spider stays independent; job storage is enabled only for `jobs`.
The shared README, source CSV, and platform detection module are unchanged.

## Installation (Windows PowerShell)

Use Python 3.11 or newer from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

SQLite is included with Python. Optional JavaScript rendering:

```powershell
.\.venv\Scripts\python -m pip install scrapy-playwright
.\.venv\Scripts\python -m playwright install chromium
```

Only add `-a render=true` for a company whose HTML requires JavaScript. API
requests never request rendering. No fixed browser waits are used.

## Sources and platform support

Read `company_name,ticker,career_site_url,job_listing_url,job_platform,source_status,sector,website_url`.
Extra columns are allowed. `job_listing_url` is the crawl entry point. By default
all source statuses are allowed for the starter dataset. Use
`-a source_status=Verified` or a comma-separated list to restrict them.
`-a career_fallback=true` explicitly permits testing/discovery from `career_site_url`.
Missing or invalid entry URLs become rejection records. Existing public functions
in `spiders/platforms.py` remain unchanged; detection uses the actual entry URL.

* Workday: paginated CXS lists, individual details, requisition ID, public posting
  URL (never a CXS detail URL), description, location, and employment type.
* Greenhouse: the public board list with `content=true` contains descriptions and
  all jobs; no undocumented pagination is added. Each job carries its actual ID
  and `absolute_url`. See [Greenhouse documentation](https://docs.greenhouse.io/job-board.html).
* Lever: public postings with `skip`/`limit` pagination; combines description,
  responsibilities/requirements lists, location, commitment, ID, and hosted URL.
  See [Lever documentation](https://github.com/lever/postings-api).
* Custom HTML: nested JSON-LD `JobPosting` objects, including `@graph` and lists.
  Without structured data, require a title, substantial description, a specific
  job URL or requisition ID, and responsibilities plus requirements sections.
  Same-host job links are followed to depth three within a scheduled-page cap.
  Careers content alone produces `job_board` or `industry_page`, not a job.

Other platforms currently use the custom HTML adapter. Location and employment
fields absent from the source remain empty; nothing is inferred from company data.

## Running and restarting

Small live prototype (the exact command used for the recorded run):

```powershell
.\.venv\Scripts\python -m scrapy crawl jobs -a only=NVDA,ABNB -a max_jobs=60 -s CLOSESPIDER_TIMEOUT=100 -s LOG_LEVEL=INFO -s JOB_OUTPUT_DIR=data/live-demo
```

Full starter/expanded dataset, with persisted Scrapy request checkpoints:

```powershell
.\.venv\Scripts\python -m scrapy crawl jobs -a max_jobs=1000 -a max_pages=100 -a source_status=Verified -s JOBDIR=data/checkpoints/full -s JOB_OUTPUT_DIR=data/full
```

Stop gracefully with Ctrl+C once; rerun the same command to resume the pending
request queue. Keep the same sources and arguments for that JOBDIR. A new refresh
crawl should use a new checkpoint directory while retaining the output directory.
SQLite commits each accepted posting immediately, so restarts do not lose saved
records. Company counters reset on restart: limits apply per process, not across
the lifetime of a checkpoint. A hard kill may lose queued requests; a fresh crawl
can rediscover them safely. Do not run two crawlers against the same output/JOBDIR.

Use `-a sources=path.csv` for another compatible source file, and `-a only=NVDA,ABNB`
for ticker selection. `max_jobs` caps examined API postings per company, including
rejected non-CS jobs; it does not guarantee that many accepted jobs.

Concurrency, timeout, per-domain delay, AutoThrottle, retries, and threshold are
Scrapy settings and can be overridden with `-s`:

```powershell
.\.venv\Scripts\python -m scrapy crawl jobs -a only=NVDA -a max_jobs=20 -s CONCURRENT_REQUESTS=4 -s CONCURRENT_REQUESTS_PER_DOMAIN=1 -s DOWNLOAD_DELAY=2 -s DOWNLOAD_TIMEOUT=30 -s RETRY_TIMES=2 -s CS_RELEVANCE_THRESHOLD=0.7
```

Defaults: eight concurrent requests, one per domain, one-second delay, AutoThrottle,
30-second timeout, two retries with nonblocking exponential waits capped at 30
seconds. Robots rules remain enabled; authentication and anti-bot blocks are not
bypassed. Scrapy logs request/item progress and exports run statistics.

## Filtering, keywords, and schema

`cs_filter.py` scores a technical role title at 0.65, distinct named technologies
at 0.05 each (maximum 0.25), and distinct technical concepts at 0.12 each
(maximum 0.35), capped at 1. The default threshold is 0.6. Evidence includes
description, responsibilities, and requirements. Excluded occupational titles
cap at 0.25 and are always rejected. Ambiguous data/security analyst and technical
support titles additionally need two technologies and a technical concept.
An incidental Python/AI mention cannot make an unrelated title pass.
Every processed valid job gets `is_cs_job`, `cs_relevance_score`, and an evidence
reason. Rejected jobs retain this information in the failure store.

This is a deterministic heuristic score, not a probability. Review false positives
and false negatives before broad use; a company sector never determines relevance.

`skill_categories.py` preserves skills, technologies, education, responsibilities,
requirements, `primary_category`, `categories`, `is_ai_job`, and `ai_signal`.
Whole-term regexes prevent Java/JavaScript and Git/GitHub substring confusion.
Technical vocabulary includes languages, frameworks, cloud, databases, DevOps,
ML, and security tools. Five basic categories remain: AI, Software Engineering,
Data, Cybersecurity, Cloud & Infrastructure; unmatched records use Other.
Multiple categories are supported; advanced clustering is deferred.

`items.JobRecord` documents the shared schema. Job and board URLs are separate,
descriptions are not truncated, and missing fields remain empty. HTML section
headings preserve line breaks for the existing section extractor. Exports retain
the original crawl timestamp in UTC ISO 8601 format.

## Deduplication and exports

`normalization.py` scopes identity to company and platform ID, falling back to a
canonical URL, then normalized title/location. URL normalization removes fragments
and known tracking parameters while preserving job-identifying queries. A capture
with no ID can merge with a posting at the same company/platform URL. Different
known IDs remain distinct, even when titles, locations, or URLs match.

`pipelines.JobPipeline` validates company, title, full description, and HTTP(S)
posting URL. Invalid records, non-CS jobs, board/industry pages, and request errors
are quarantined with reasons. Duplicates refresh their SQLite payload; they do not
produce a second insert or feed row. URL-less records have a fallback identity
utility but are rejected by the production pipeline.

Each output directory contains `jobs.sqlite3`, `cs_jobs.json`, `cs_jobs.csv`,
`cs_jobs.jsonl`, `crawl_failures.csv`, and `crawl_summary.json`. Export replacement
is atomic per file. CSV list values are JSON strings. SQLite is authoritative
after interruption; rerunning a crawl regenerates exports. Use these accumulated
exports for incremental data, since `-O` feeds contain only newly accepted items.
The dataset records observations; refreshes do not mark expired jobs as closed.

## Tests and demonstration evidence

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m industry_crawler.demo
```

The offline demo reads starter companies, reanalyzes captured descriptions, applies
filtering, rejects two captured non-CS records, deliberately repeats one job to
exercise deduplication, and produces all exports in `data/fixture-demo`. It is
explicitly labeled snapshot replay, not a fresh successful crawl. A second run
updates the same database. Synthetic API/HTML unit fixtures also cover Lever,
which has no company in the current starter file.

Validation on Python 3.14 / Scrapy 2.19: `pytest -q` reported **36 passed**;
`ruff check .` reported **All checks passed**. Ruff checks basic syntax/imports
across the repository with two pre-existing unused-import exceptions in untouched
scaffold/industry files. Repeating the offline demo retained 12 rows and reported
13 duplicate updates on the second run. A no-source startup/export smoke check
also completed. Optional rendering and a 500-company crawl were not tested.

The October 11, 2026 live run returned 42 CS postings: 36 NVIDIA (Workday), six
Airbnb (Greenhouse). It examined 120 postings and rejected 78, completed in 82.9
seconds, and received 66 HTTP 200 responses including robots requests. See
`docs/live-demo-summary.json`. Twelve actual accepted jobs and two actual rejected
records were saved in `tests/fixtures/captured_jobs.json` for reproducible replay.
Posting availability can change; these snapshots are not current vacancies.

## Limitations and integration contract

Workday CXS is a publicly accessible site API, not a stable vendor contract.
Tenant formats unsupported by the existing helper may require source-owner work.
Generic HTML extraction is conservative, limited to same-host links and common
schema/section headings. JavaScript rendering is opt-in and has not been exercised
in the live prototype. API descriptions may contain employer boilerplate. Section
extraction and category detection remain heuristic. Missing Workday paths or malformed
API responses are quarantined as extraction failures rather than successful records.
Authentication, restrictions, or unavailable APIs may prevent collection.

The source developer should provide validated individual board URLs and maintain
the existing `platforms.py` public functions; no source discovery or validation
files were changed. More ATS-specific adapters, durable lifetime company counters,
closed-job reconciliation, and multilingual parsing remain future work.

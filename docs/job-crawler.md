# CS job crawler reference

The jobs spider reads `company_sources.csv` through `company_input.py`. Source
research and review are manual; see [manual sources](manual-sources.md). The `jobs` spider is the only crawler entry point.

## Adapters and record fields

- Workday: paginated CXS lists and individual details, requisition IDs, public
  posting URLs, full descriptions, locations and employment types.
- Greenhouse: public board jobs with `content=true`, IDs, posting URLs,
  descriptions and locations. No undocumented pagination is added.
- Lever: `skip`/`limit` pagination, description and section lists, IDs, hosted
  URLs, locations and commitment types.
- SmartRecruiters: public employer Posting API lists with `limit`/`offset` and
  `totalFound`, then an individual detail request for each posting. Captures ID,
  employer metadata, location, employment label, posting URL, reference number
  and all text sections of the job ad. Uses fixed documented endpoint paths;
  does not follow arbitrary API `ref` URLs. No credentials or paid service.
  See [endpoints](https://developers.smartrecruiters.com/docs/endpoints) and
  [objects](https://developers.smartrecruiters.com/docs/objects).
- Generic HTML: nested JSON-LD `JobPosting` records, including lists and `@graph`.
  Without structured data, requires a title, substantial text, a specific posting
  URL or requisition ID, and responsibilities plus requirements sections.

Other detected platforms use generic HTML. Provider implementations live in
`industry_crawler/adapters/`, selected through its registry. Existing spider
callbacks and platform helpers remain public and checkpoint-compatible. Same-host job links are followed to
depth three within a scheduled-page budget. Careers information without job
evidence is recorded as a board/non-job page and rejected by the jobs pipeline.
Location and employment fields absent from the source remain empty.

Optional `scrapy-playwright` rendering requires `-a render=true` and installed
Chromium. API requests never request rendering. Browser rendering is not proven
by the local tests and does not bypass access restrictions.

## Arguments and input handling

| Argument | Default / purpose |
| --- | --- |
| `sources` | Repository-root `company_sources.csv`. |
| `only` | All companies; otherwise comma-separated primary tickers. |
| `max_jobs` | 200 examined postings per company, including rejected jobs. |
| `max_pages` | 30 scheduled generic HTML pages per company. |
| `source_status` | No status filter; optional explicit status selection. |
| `render` | `false`; opt into installed Playwright for HTML requests. |

Missing `job_listing_url` values are always skipped, even when corporate or
careers URLs are present. There is no fallback or source discovery. Invalid/excluded rows are skipped regardless of a requested status
filter. The CSV is read-only. Alternate `sources` CSVs may only select company
(ticker)/listing-URL pairs already present in the repository dataset. Newly
submitted sites need manual review and a repository change before collection.
Requests and redirects are constrained to the selected listing host and fixed
API hosts; unsafe URL schemes, credentials, local/private IP literals and unusual
ports are rejected. Off-host career redirects may require a reviewed CSV correction. Missing files, malformed headers/rows and invalid
entry URLs are recorded with reasons; valid companies continue.

```powershell
python -m scrapy crawl jobs -a only=NVDA,ADBE -a max_jobs=20 -a max_pages=10 -s JOB_OUTPUT_DIR=data/sample
python -m scrapy crawl jobs -a max_jobs=1000 -a max_pages=100 -s JOBDIR=data/checkpoints/full -s JOB_OUTPUT_DIR=data/full
```

Use Ctrl+C once for a graceful stop. Rerun the same checkpoint command with the
same sources and options to resume. Use a new checkpoint for a new refresh;
counters reset per process. Do not share an output/checkpoint directory between
concurrent crawlers. Scrapy settings control concurrency, delays, timeout, retries
and `CS_RELEVANCE_THRESHOLD`. Robots rules, AutoThrottle and retry backoff remain
enabled; authentication and anti-bot protections are not bypassed.

## Filtering, analysis and storage

The analyzer extracts predefined skills, technologies, education and sections,
and assigns weighted keyword categories. The pipeline validates company, title,
description and HTTP(S) posting URL, then applies the existing CS score: a technical
title contributes 0.65, technologies up to 0.25 and technical concepts up to 0.35,
capped at one. Default threshold is 0.6. Excluded occupations are rejected;
ambiguous analyst/support titles need two technologies and a technical concept.
The score is a heuristic, not a probability. Non-CS records retain an explanation
in failure storage. Responsibilities/requirements snippets are bounded to 1,500
characters; full job descriptions are preserved.

Company identity uses normalized tickers (including dot/hyphen variants), or a
normalized name when no ticker exists. Shared normalization cleans company names,
titles, locations/state components, posting URLs, malformed fields and employment
types. Changed values remain in `original_values`; unknown employment labels are
retained. Full descriptions keep their line breaks. Location normalization is
conservative and does not establish identity by itself.

Storage prefers company-scoped platform IDs, then explicit employer requisition
IDs, then exact canonical posting URLs when at least one capture lacks an ID.
Workday `jobReqId`, Greenhouse `requisition_id` and SmartRecruiters `refNumber`
provide explicit reference evidence; IDs are never guessed from titles. Distinct
known IDs on the same platform, or conflicting explicit references, prevent a
merge. Known IDs on different platforms do not merge merely because a URL or title
matches. Shared employer reference evidence can verify a cross-platform match.
Stronger ID evidence takes priority over a weaker URL match. Multiple equally
strong candidates remain separate, with `duplicate_review` containing stored
identity keys for manual review. Matching titles/locations only create review
hints; they never cause automatic merging. Identity-less direct storage calls
also remain separate; the pipeline requires a valid posting URL.

`first_seen` survives updates, `last_seen` records the latest observation time,
and `source_platform`/`source_job_id` expose provider identity. Unique
`source_observations` preserve each original source, URL and ID across merges.
An ID-less refresh retains the representative known provider ID. Missing fields
do not erase known values. Duplicate updates retain the existing SQLite primary
key and do not produce a new feed row. Existing databases are indexed additively
through `job_companies`; original rows are untouched until refreshed. Historical
duplicates are not deleted or automatically consolidated. Back up research data
before any future manual reconciliation.

Each output directory contains `jobs.sqlite3`, `cs_jobs.json`, `cs_jobs.csv`,
`cs_jobs.jsonl`, `crawl_failures.csv` and `crawl_summary.json`. Accepted postings
commit immediately; exports replace individual files atomically at spider close.
CSV list values are JSON strings. The summary's `sources` section stores selected,
scheduled, missing, invalid, excluded and input-error counts plus skipped company
identities/reasons. Failures are separate from accepted CS jobs.

`-O data/output.json` adds a feed of newly accepted jobs for the current run. Accumulated
`cs_jobs.*` exports include refreshed stored jobs. SQLite is authoritative after
interruption; rerunning a crawl regenerates exports. Closed jobs are not reconciled.

## Tests and evidence

```powershell
python -m pytest -q
python -m ruff check .
python -m industry_crawler.demo --output data/fixture-demo
```

Tests use mocked API/HTML records, captured descriptions and temporary storage.
The demo replays those fixtures and tests rejection/duplicates; it is not a live
crawl and does not demonstrate current access to employer sites. Tests cover
input immutability, missing listing URLs without fallback, preserved U.S. query
filters, API pagination, individual extraction, CS filtering and storage/exports.

Workday requests preserve supplied country/location facet IDs and keyword search
across API pagination. Workday locale segments such as `en-US` select a language,
not a country filter. The crawler does not invent country identifiers. Other API
adapters currently fetch their public boards; a board URL's location filter does
not guarantee that every extracted record is U.S.-based. Review job locations in
the resulting research data.

Limits include changing APIs, unsupported tenant formats, dynamic or multilingual
HTML, blocked sites, heuristic category/filter accuracy, per-process restart
counters and missing closed-job reconciliation. Advanced clustering and academic
integration remain future work.

## Integration review (SCRUM-194 / SCRUM-207)

The reviewed implementation already supplied useful Workday list/detail and Lever
pagination, Greenhouse `content=true`, strict generic HTML job evidence, CS
filtering, skill extraction, retry backoff and transactional exports. These were
reused. Provider routing/parsing previously lived in one spider; malformed API
containers could terminate a callback, and duplicate refreshes replaced payloads
without first/last timestamps or retained cross-source evidence. The adapters,
validation, provenance and conservative matching address those gaps.

Workday CXS remains an employer-site API without a stable vendor contract.
Greenhouse returns the public board in one request; employment type is left empty
when not supplied. Lever uses the existing public `.co` endpoint; regional/custom
tenants may need a separately reviewed adapter change. SmartRecruiters requires
an identifiable company path and published public job ads; private/inactive jobs
and missing detail URLs/descriptions are not invented. Missing required fields
are rejected by the pipeline. API decode/container failures are quarantined by
`JobErrorMiddleware`; HTTP errors use the existing Scrapy retry and errback path.
Robots, concurrency, delays and retry settings remain enabled. Browser rendering,
current live access and a full 500-company crawl were not tested in this change.

See [adapter contributions](job-adapters.md) for the extension contract, an
example and the manually reviewed source proposal process.

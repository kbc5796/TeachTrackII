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
- Generic HTML: nested JSON-LD `JobPosting` records, including lists and `@graph`.
  Without structured data, requires a title, substantial text, a specific posting
  URL or requisition ID, and responsibilities plus requirements sections.

Other detected platforms use generic HTML. Same-host job links are followed to
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
filter. The CSV is read-only. Missing files, malformed headers/rows and invalid
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

Identity is company-scoped and prefers platform ID, then canonical URL; fallback
identity utilities use normalized title/location. Production records without a
valid posting URL are rejected. Canonical URLs remove fragments and known tracking
parameters. ID-less observations can merge with a known posting at the same
company/platform URL; different known IDs remain distinct. Duplicates update
SQLite and do not generate a second insert/feed row.

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

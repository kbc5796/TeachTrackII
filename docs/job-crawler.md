# CS job crawler reference

The jobs spider reads `company_sources.csv` through `company_input.py`. Source
research and review are manual; see [manual sources](manual-sources.md). The
independent `industry` spider collects website research pages and does not use
the CS job persistence pipeline.

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
evidence is recorded as a board/industry page and rejected by the jobs pipeline.
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
| `career_fallback` | `false`; legacy explicit fallback to a career navigation URL. |

Normal manual-source runs leave `career_fallback=false` so missing listing URLs
are skipped. Invalid/excluded rows are skipped regardless of a requested status
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

`-O output.json` adds a feed of newly accepted jobs for the current run. Accumulated
`cs_jobs.*` exports include refreshed stored jobs. SQLite is authoritative after
interruption; rerunning a crawl regenerates exports. Closed jobs are not reconciled.

## Tests and evidence

```powershell
python -m pytest -q
python -m ruff check .
python -m industry_crawler.demo --output data/fixture-demo
```

Tests use mocked API/HTML records and temporary storage. The demo replays captured
jobs and tests rejection/duplicates; it is not a live crawl. The preserved
[live prototype summary](live-demo-summary.json) records an October 11, 2026
NVIDIA/Airbnb run, not current availability. Historical source status evidence is
not manual verification.

During the manual-source refactor on October 11, 2026, pytest reported 58 passing
tests and Ruff passed. A new live run selected NVIDIA, Airbnb and 3M using the
existing working CSV: five NVIDIA jobs were accepted, five postings were rejected
as non-CS, four Airbnb pages were rejected as board records, and 3M was skipped
for a missing listing URL. All six SQLite/export files were generated. Repeating
the same run updated five duplicates and retained five stored jobs. The CSV and
other pre-existing dataset edits were byte-for-byte unchanged. These observations
do not demonstrate browser rendering or successful extraction across all employers.

Separately, the offline CSV-to-Workday-callback-to-pipeline test used captured
descriptions and mocked API responses. It retained one annotated CS job across
two passes, rejected a non-CS record and a missing source, and observed three
duplicate updates. No live request or current job availability is implied by that
fixture result.

Limits include changing APIs, unsupported tenant formats, dynamic or multilingual
HTML, blocked sites, heuristic category/filter accuracy, per-process restart
counters and missing closed-job reconciliation. Advanced clustering and academic
integration remain future work.

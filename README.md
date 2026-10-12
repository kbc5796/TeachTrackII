# TeachTrack II Industry Crawler

TeachTrack II is a university research project connecting Computer Science coursework with industry job requirements. This repository crawls individual job postings from a **manually maintained S&P 500 company-source CSV**, retains CS-relevant jobs, extracts technical keywords, assigns preliminary categories, and exports structured research data.

Our team researches and verifies company job-board links manually. The crawler consumes those links as input and never updates the company CSV. The separate [academic crawler](https://github.com/GeovanieVasquez/cs-scraper-build) will eventually contribute course topics and skills to a knowledge graph alongside these industry records. Academic integration, knowledge graph construction and advanced ML clustering are future work.

## Contents

- [Manually collect company job-board URLs](#manually-collect-company-job-board-urls)
- [Install dependencies](#install-dependencies)
- [Run the industry crawler](#run-the-industry-crawler)
- [CS job filtering](#cs-job-filtering)
- [Keyword extraction](#keyword-extraction)
- [Categories](#categories)
- [Storage and output](#storage-and-output)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Limitations and future work](#limitations-and-future-work)

## Manually collect company job-board URLs

The workflow is: manually research company sources → edit `company_sources.csv` → crawl individual postings → filter CS relevance → save technical keywords/categories → export research data.

The committed source dataset contains **500 companies representing 503 tickers**, with **202 collected listing URLs and 298 missing listing URLs**. These are October 2026 collection counts, not a claim that all sources are current or manually verified. Local team edits may increase coverage. Keep every company row even when its URL is missing.

Open `company_sources.csv` in a text editor or spreadsheet application. Preserve this header and all existing additional columns:

```csv
company_name,ticker,career_site_url,job_listing_url,job_platform,source_status,sector,website_url
```

| Field | Team maintenance |
| --- | --- |
| `company_name`, `ticker` | Keep the existing issuer identity. The additional `tickers` column preserves multiple share classes. |
| `website_url` | Official company homepage; it is not the jobs crawl entry point. |
| `career_site_url` | Official careers navigation page; it may contain company information without actual vacancies. |
| `job_listing_url` | Direct official job-search/listing page or employer ATS board. Leave it empty when the team has not found a suitable URL. |
| `job_platform` | Optional platform annotation. The jobs spider detects its adapter from the entry URL. |
| `source_status` | Maintain the team's review status after manual research; it does not need to be Verified for crawling. |

For each company, follow its official careers links, confirm that the destination displays actual job listings for that employer, and copy the exact URL. A corporate homepage or generic careers information page is not a replacement for a listing URL. Retain CSV quoting for names/values containing commas, and save as UTF-8 CSV without changing ticker text or deleting additional columns.

Existing `source_status`, `last_verification_date`, `evidence` and related fields originate from historical automated runs. **Their existing Verified labels are not manual verification.** When reviewing a row, update its status and add optional `reviewed_by` and `manual_review_date` columns to record the human review separately. Keep historical fields for reference. Suggested manual statuses include Needs Review, Verified, Blocked, No Listings Found, Invalid and Excluded.

The default jobs crawl attempts every selected company with a valid-looking listing URL, regardless of historical Verified/Unverified/Blocked labels. It skips missing URLs, malformed inputs, and rows explicitly marked `Invalid` or `Excluded`; an optional `excluded` column set to `true`, `yes` or `1` also excludes a row. Skips are recorded with company identity and reason. Network failures are logged and stored without editing the CSV.

See [the manual source guide](docs/manual-sources.md) for collaboration and review details. The files under `data/sp500/` and `reports/sources/` are preserved historical artifacts, not active source-management tools or current manual review results.

## Install dependencies

Use Python 3.11 or newer, Git and Windows PowerShell:

```powershell
git clone https://github.com/kbc5796/TeachTrackII.git
Set-Location TeachTrackII
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If activation is unavailable, use `.\.venv\Scripts\python.exe` instead of `python` in subsequent commands. SQLite is included with Python. Scrapy and the test/lint dependencies are declared in `requirements.txt`.

### Optional browser rendering

Workday, Greenhouse and Lever API requests do not require a browser. For HTML sites that need JavaScript rendering, install:

```powershell
python -m pip install scrapy-playwright
python -m playwright install chromium
```

Add `-a render=true` to a jobs crawl only when needed. Playwright rendering is opt-in and does not bypass robots, authentication or CAPTCHA restrictions. Browser compatibility has not been verified by the automated fixture tests.

## Run the industry crawler

Run these commands from the repository root with the environment active. Use the **`jobs` spider** for CS job collection.

One company:

```powershell
python -m scrapy crawl jobs -a only=NVDA -a max_jobs=20 -s JOB_OUTPUT_DIR=data/nvidia
```

Multiple companies:

```powershell
python -m scrapy crawl jobs -a only=NVDA,ADBE -a max_jobs=30 -a max_pages=15 -s JOB_OUTPUT_DIR=data/sample
```

Limited test crawl, including 3M as a missing-URL example in the saved dataset:

```powershell
python -m scrapy crawl jobs -a only=NVDA,ABNB,MMM -a max_jobs=10 -a max_pages=5 -s CLOSESPIDER_TIMEOUT=75 -s JOB_OUTPUT_DIR=data/test-crawl
```

Larger crawl of all companies with usable listing URLs:

```powershell
python -m scrapy crawl jobs -a max_jobs=1000 -a max_pages=100 -s JOBDIR=data/checkpoints/full -s JOB_OUTPUT_DIR=data/full
```

`max_jobs` limits examined postings per company, including jobs later rejected as non-CS; it is not a target number of accepted jobs. `max_pages` limits scheduled generic HTML pages. `only` selects primary tickers; `-a sources=path/to/companies.csv` selects another compatible input file. `-s CS_RELEVANCE_THRESHOLD=0.7` changes the relevance threshold. The optional `source_status` argument can restrict a run explicitly, but no Verified restriction is enabled by default.

For a checkpointed crawl, stop gracefully with Ctrl+C once, then rerun the same command with the same sources and arguments. For a new refresh, use a new checkpoint directory. Per-company counters reset on process restart. Only one crawler should use each output/checkpoint directory at a time.

The separate `industry` spider remains available for company website research pages. It is not the individual-job/CS storage workflow described here; see [job crawler details](docs/job-crawler.md).

## CS job filtering

`cs_filter.py` applies a weighted, explainable relevance score based on technical role titles, named technologies, and technical concepts in the description, responsibilities and requirements. The default threshold is `0.6`. Excluded occupational titles are rejected, and ambiguous analyst/support roles need additional technical evidence.

The existing rules cover software engineering, AI/ML, data engineering/science, cybersecurity, cloud, DevOps, site reliability, network, systems, database and technical IT roles. Unrelated retail, sales, healthcare, accounting, manufacturing and other non-CS postings are rejected. Each processed valid job receives `is_cs_job`, `cs_relevance_score` and `cs_filter_reason`; rejected records retain their reason in failure storage. Company sector does not determine relevance. This heuristic requires review for false positives and negatives.

## Keyword extraction

`skill_categories.py` uses predefined vocabularies and term-boundary regexes to extract programming languages, frameworks, cloud platforms, databases, DevOps tools, AI/ML technologies, cybersecurity tools, technical skills and education requirements. Examples include Python, Java, React, AWS, SQL, Docker, Kubernetes, PyTorch and security tools.

The analyzer also extracts responsibilities and qualifications/requirements from recognizable section headings. Job descriptions are retained in full; extracted section snippets are bounded. Missing source fields remain empty rather than being inferred. Rules reduce substring confusion such as Java versus JavaScript but do not provide complete multilingual or semantic understanding.

## Categories

Jobs may receive multiple preliminary categories with a primary category:

- AI
- Software Engineering
- Data
- Cybersecurity
- Cloud & Infrastructure

Unmatched records use Other. AI indicators include `is_ai_job` and `ai_signal`. Categories are keyword-based; advanced clustering is not implemented.

## Storage and output

The jobs pipeline validates individual records, applies CS filtering, normalizes URLs, deduplicates accepted postings and stores them transactionally in SQLite. Company name, ticker, sector, platform, posting/board URLs, job identity, title, location, employment type, technical annotations and UTC crawl timestamps are retained for future academic integration.

Each `JOB_OUTPUT_DIR` contains:

| File | Contents |
| --- | --- |
| `jobs.sqlite3` | Authoritative accumulated accepted jobs and rejected/error records. |
| `cs_jobs.json` | Accepted CS jobs as a JSON array. |
| `cs_jobs.csv` | Tabular jobs; list/dict fields are encoded as JSON strings. |
| `cs_jobs.jsonl` | One accepted job per line. |
| `crawl_failures.csv` | Non-CS jobs, invalid records, skipped sources and request/extraction failures. |
| `crawl_summary.json` | Stored totals, crawl statistics, and a `sources` section listing skipped companies and reasons. |

Exports are generated at spider close and replaced atomically per file. Accepted records commit immediately; SQLite remains authoritative after interruption. Duplicates refresh stored payloads rather than adding another posting. Different known platform job IDs remain distinct. Refreshing a crawl does not reconcile closed/expired jobs.

An additional Scrapy feed can export newly accepted items from one run:

```powershell
python -m scrapy crawl jobs -a only=NVDA -a max_jobs=20 -s JOB_OUTPUT_DIR=data/export-demo -O data/export-demo/new_jobs.json
```

Use `.csv` or `.jsonl` for other feed formats. `-O` overwrites that feed, and duplicate updates do not produce new feed rows; use `cs_jobs.*` for accumulated output. Historical root-level JSON captures are not current validated CS-only datasets.

## Testing

```powershell
python -m pytest -q
python -m ruff check .
git diff --check
```

Tests cover Workday/Greenhouse/Lever and custom extraction, pagination, platform detection, weighted filtering, keywords/categories, deduplication, storage/exports, CSV structure, missing and invalid sources, explicit exclusions, and input immutability. They use local fixtures and temporary outputs; passing tests does not establish current access to live job boards.

Replay captured jobs through filtering and exports without contacting employers:

```powershell
python -m industry_crawler.demo --output data/fixture-demo
```

This is a captured-snapshot demonstration, not a fresh live crawl. See [testing and crawl details](docs/job-crawler.md) for the distinction.

## Project structure

| Path | Purpose |
| --- | --- |
| `company_sources.csv` | Team-maintained company/source input, including historical metadata. |
| `industry_crawler/company_input.py` | Read-only CSV structure checks; no HTTP requests or source updates. |
| `industry_crawler/spiders/jobs.py` | Individual job adapters, pagination and missing-source handling. |
| `industry_crawler/spiders/platforms.py` | Platform detection, API helpers and HTML text utilities. |
| `industry_crawler/spiders/skill_categories.py` | Keywords, education, sections and preliminary categories. |
| `industry_crawler/cs_filter.py` | Weighted CS relevance scoring. |
| `industry_crawler/pipelines.py`, `storage.py`, `normalization.py` | Validation, deduplication, SQLite and exports. |
| `industry_crawler/settings.py`, `retry.py`, `job_errors.py` | Responsible crawling, retries and failure handling. |
| `industry_crawler/spiders/industry.py` | Independent company website research spider. |
| `tests/` | Job, CSV and platform tests, plus captured job fixtures. |
| `requirements.txt`, `pyproject.toml`, `scrapy.cfg` | Dependencies, checks and Scrapy configuration. |
| `docs/manual-sources.md`, `docs/job-crawler.md` | Manual source workflow and crawler reference. |
| `data/sp500/`, `reports/sources/` | Preserved historical data; no active generators remain. |

## Limitations and future work

The company CSV is intentionally incomplete and needs manual research. A saved listing URL or historical Verified label does not guarantee current access. Workday, Greenhouse and Lever have API extraction adapters; SmartRecruiters, iCIMS, Taleo, Oracle Recruiting, SAP SuccessFactors, Eightfold and Custom sites rely on generic HTML support and may require adapter work. Detection is not a guarantee of extraction.

JavaScript-heavy websites, unsupported layouts, changing APIs, robots restrictions, rate limiting, authentication and blocked boards can prevent collection. Generic HTML follows same-host links within depth/page limits. The crawler records failures and continues without changing sources or bypassing restrictions.

Future work includes completing manually reviewed source coverage, improving extraction and CS filtering accuracy, refining preliminary categories, integrating the academic crawler, and building the research knowledge graph. Advanced AI/ML grouping is deferred.

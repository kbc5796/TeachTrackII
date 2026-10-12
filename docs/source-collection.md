# S&P 500 source collection

This workflow extends the existing crawler and preserves its original CSV columns.
The initial audit found 11 seeded companies, reusable CSV readers and platform/API
helpers, and no constituent importer, evidence-based validator, reports or tests.
`jobs.py`, skill classification, extraction pipelines and the shared README are unchanged.
The original seed URLs remain available; their historical validation labels are
reset when there is no dated evidence from this system.

## Observed run: October 11, 2026 (America/Chicago)

Imported the October 9 upstream snapshot, commit
`36472b57910842f4025e8ca8303b93a692d2bbae`: 503 tickers representing 500 unique
companies. Discovery attempted all 500 companies and obtained 499 published
website URLs and 204 candidate listing URLs. Validation rejected two candidates,
leaving 202 listing URLs (11 original seeds and 191 additional URLs).

| Result | Companies |
| --- | ---: |
| Verified | 56 |
| Unverified | 43 |
| Needs Review | 363 |
| Blocked | 34 |
| Invalid | 0 |
| No Listings Found | 4 |
| All unresolved / manual review | 444 |

Every row received a validation attempt timestamp, including records without a
URL. Missing URLs are not live HTTP verifications. The target of approximately
500 verified sources has **not** been reached. The saved artifacts describe
this measured run; later network runs can produce different counts.

The source-related Ruff check passed and all 58 pytest cases passed. Protected
job extraction/filtering files and the shared README have no diff. Work is on
`feature/sp500-sources` and has not been pushed.

## Installation (Windows PowerShell, Python 3.11+)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-sources.txt
```

## Update the company list

```powershell
python -m industry_crawler.sources import
```

The importer checks the most recent commit affecting `data/constituents.csv` in
[datasets/s-and-p-500-companies](https://github.com/datasets/s-and-p-500-companies).
It refuses an upstream snapshot older than 14 days or an unexpected ticker count.
It downloads a commit-pinned CSV, storing its date, retrieval timestamp and SHA-256
in `data/sp500/provenance.json`. The source is maintained from Wikipedia, rather
than an S&P licensed feed; a recent commit establishes freshness of the upstream
snapshot, not independent confirmation of every index change.

CIK identifies unique issuers; normalized names are the fallback. Multiple share
classes become one company row. `ticker` retains the existing crawler's primary
symbol and `tickers` contains all symbols separated by `|`. Seed fields and extra
CSV columns survive the merge. Companies no longer in the index are removed from
the active list; Git history and the original backup retain earlier data.

Reproduce the saved snapshot without network access:

```powershell
python -m industry_crawler.sources import --offline
```

## Discover sources

```powershell
python -m industry_crawler.sources discover --workers 8 --timeout 8
```

The Wikipedia constituent table supplies company article links by ticker. Company
infoboxes supply website URLs. Discovery starts from existing job/career URLs and
the published website, following observed career and job links with an eight-page
budget per company. It records article and link provenance. It saves observed ATS
board/search URLs as candidates, or pages displaying company-matched job listings;
it never fabricates domains or company-specific URL paths. Authentication,
registration and talent-community links are excluded. Generic career landing
pages are navigation seeds, not accepted job-board evidence.

Discovery does not mark a source Verified. Run validation afterwards. Missing
article mappings, blocked sites, JavaScript rendering, unrelated links and the
bounded search budget can require manual discovery. Published Wikipedia website
links should also be reviewed when an issuer has changed names or ownership.

## Validate and report

```powershell
python -m industry_crawler.sources validate --workers 8 --timeout 8
python -m industry_crawler.sources report
```

Requests use `TeachTrackSourceBot/1.0`. For research deployment provide a real
contact through `--user-agent 'TeachTrackSourceBot/1.0 (contact: your-address)'`.
Every origin has serial requests with at least a one-second delay; longer robots
crawl delays and request-rate directives are honored. Robots redirects are
followed with a bound; inaccessible policies fail closed. Destination policies
are checked before requesting redirect targets. HTTP 429 suspends that origin
for the run. No authentication, CAPTCHA solving or anti-bot bypass is attempted.
Nonpublic resolved addresses, URL credentials, nonstandard ports and HTTPS
downgrades are rejected. Responses are limited to 4 MB and redirects to five.

HTTP 200 is insufficient. HTML evidence requires populated JobPosting structured
data with matching hiringOrganization. Individual job links with a matching
company title/heading require accessible company-matched JobPosting detail-page
evidence (up to two detail pages are checked). Workday, Greenhouse and Lever have read-only API checks
for populated listing records and an exact normalized company tenant identifier.
Abbreviated or differently named tenants require review. Workday's alternate
`myworkdaysite.com` layout needs manual API mapping. This is conservative
automated identity evidence, not a guarantee of employer ownership.

Statuses:

| Status | Meaning |
| --- | --- |
| Verified | Accessible listing evidence and matching company identity |
| Unverified | Network/server problem or no accessible listing evidence |
| Needs Review | Missing source, ambiguous identity, redirect or API mapping |
| Blocked | Robots restriction, HTTP 401/403/429, authentication or CAPTCHA |
| Invalid | Invalid/unsafe URL or HTTP 404/410 |
| No Listings Found | Page or recognized API explicitly reports no listings |

The validator writes UTC attempt timestamps, final resolved board URLs, platform,
evidence and failure reasons. Original discovery reasons are retained in
`discovery_error` when the first validation runs. A timestamp records an attempt, not successful
verification. It checkpoints each completed company; a partial run preserves
other rows and does not imply they were verified in that run.

`reports/sources/summary.json` contains company and ticker totals, URL counts,
all six status counts, platform totals and review counts.
`validation.csv` contains every company; `manual-review.csv` contains every
non-Verified company. The JSON crawler coverage metric counts companies outside
Workday/Greenhouse/Lever, including missing/unknown platforms. Other detected
platforms still use the existing crawler's generic HTML fallback.

## Review failures and rerun a subset

```powershell
Import-Csv reports/sources/manual-review.csv | Select-Object company_name,ticker,source_status,failure_reason
python -m industry_crawler.sources discover --only MSFT,NVDA
python -m industry_crawler.sources validate --only MSFT,NVDA
python -m industry_crawler.sources report
```

Use official employer pages to find missing sources. Populate `website_url`,
`career_site_url`, `job_listing_url` and `discovery_provenance` with observed URLs;
rerun validation. Do not change a status to Verified merely because a URL looks
plausible. A browser-only board remains Unverified until its evidence can be
reviewed. `--limit 10` allows a small trial without changing unselected rows.

## Platform coverage

Detection supports Workday, Greenhouse, Lever, SmartRecruiters, iCIMS, Taleo,
Oracle Recruiting (`Oracle Cloud`, retained for compatibility), SAP SuccessFactors
(`SuccessFactors`), Eightfold, and Custom. Detection checks domain boundaries and
embedded vendor URLs. Detection is separate from extraction/verification support:
only the first three have API validation; the others require HTML listing evidence
or manual review. Existing public platform helpers are preserved.

## Deterministic verification

```powershell
python -m ruff check industry_crawler/sources industry_crawler/spiders/platforms.py tests
python -m pytest -q
```

Tests mock HTTP pages, redirects, robots policies, upstream metadata and APIs.
They cover import, share-class deduplication, CSV quoting, URL normalization,
platform boundaries, listing/identity evidence, statuses, missing sources,
discovery, redirects and reports. They do not claim live extraction success.

## Limitations

The 500 verified-board target is an objective, not an asserted result. Inspect the
generated report for the actual counts. Discovery has bounded coverage and cannot
render JavaScript or access blocked sites. Static individual-job links are a
heuristic and merit review if a site's structure is unusual. Index freshness is
checked against upstream commit metadata, not a licensed S&P feed. The academic
reference repository was unavailable through the browser during the audit; no
integration with its code is claimed. No changes have been pushed.

# Maintaining company sources manually

`company_sources.csv` is human-maintained, read-only crawler input. The crawler
starts exclusively from `job_listing_url`; it never derives a board from a
corporate website or careers page and never rewrites the CSV.

## Preserve company identity

Keep all 500 companies in their existing order. Keep names, primary tickers,
additional share-class `tickers`, sectors and official corporate website URLs.
Do not remove a company because its listing URL is missing.

## Research and edit a source

1. Follow the employer's official careers links in a browser.
2. Find its direct search/results page with actual vacancies. Employer-specific
   Workday, Greenhouse, Lever, Oracle, iCIMS and other official ATS boards are valid.
3. Prefer a stable United States filter; copy its observed URL including query
   parameters. A locale such as `en-US` alone is not a U.S. location filter.
4. If a stable U.S. filter cannot be confirmed, use the direct official search
   page and inspect locations. Leave the field blank if the board itself is uncertain.
5. Keep careers information in `career_site_url` and corporate pages in
   `website_url`. Never use aggregators, individual postings, login pages, job
   alerts, talent-network forms or generic careers landing pages as listing URLs.
6. Update `job_platform` when confirmed. Mark `source_status` for manual review;
   optional `reviewed_by` and `manual_review_date` columns can record the reviewer.
7. Save UTF-8 CSV with quoting intact and review the Git diff for changed identity,
   lost filter parameters, row order or unintended data edits.

For Apple, keep the separate careers navigation page and use:

```text
https://jobs.apple.com/en-us/search?location=united-states-USA
```

## Columns and runtime behavior

```csv
company_name,ticker,career_site_url,job_listing_url,job_platform,source_status,sector,website_url,tickers
```

The reader requires unique `company_name`, `ticker` and `job_listing_url` headers.
Other named columns are allowed. It checks structure locally; malformed rows are
reported without preventing valid companies from being crawled.

Statuses are manual annotations. Every valid supplied listing URL is attempted
by default; Verified is not required. Use Invalid or Excluded to skip a company,
or add `excluded=true`. Missing and syntactically invalid listing URLs are always
skipped and reported with company identity and reason. Blocked and Unverified
alone do not prevent a crawl attempt. Access restrictions remain respected.

## Review missing sources

```powershell
Import-Csv company_sources.csv | Where-Object { -not $_.job_listing_url } | Select-Object company_name,ticker
```

## Test an edited source

```powershell
python -m scrapy crawl jobs -a only=NVDA -a max_jobs=10 -s JOB_OUTPUT_DIR=data/source-edit-test
```

Replace NVDA with the edited primary ticker. Inspect `crawl_summary.json`'s
`sources` section for scheduled and skipped companies and `crawl_failures.csv`
for crawl/extraction failures. Research corrections manually. Outputs and
checkpoints belong under the ignored `data/` directory.

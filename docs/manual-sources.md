# Maintaining company sources manually

`company_sources.csv` is the primary input. Team members research and review URLs;
the crawler only reads the file. No importer, website discovery, network source
verification service or source-report generator is included.

## Preserve the existing company list

Keep all 500 company records, all existing listing URLs, the additional `tickers`
column representing 503 share-class symbols, and the remaining historical fields.
The committed October 2026 dataset contains 202 listing URLs and 298 empty ones.
Do not remove a company just because it has no usable URL yet. Local corrections
may differ from that historical snapshot.

## Review a company

1. Open the CSV in a UTF-8 text editor or spreadsheet application.
2. Research the employer's official website and follow its official careers links.
3. Find the actual job-search/listing page, including an employer-specific ATS board.
4. Confirm in the browser that it belongs to the intended company and shows jobs.
5. Update that company's `job_listing_url` with the exact observed URL. A homepage
   or careers information page goes in `website_url` or `career_site_url` instead.
6. Set `source_status` for your review and record optional `reviewed_by` and
   `manual_review_date` fields. Leave missing URLs empty; do not guess them.
7. Save as UTF-8 CSV, preserving quoted commas and the existing columns. Review the
   Git diff for unintended spreadsheet formatting or lost rows before sharing.

Agree on company/ticker ownership within the team to avoid conflicting CSV edits.

## Columns and runtime behavior

Recommended base columns:

```csv
company_name,ticker,career_site_url,job_listing_url,job_platform,source_status,sector,website_url
```

The reader requires unique `company_name`, `ticker` and `job_listing_url` headers;
other named columns are allowed. It checks structure locally without contacting
the company. A missing header is reported as an input error, while a malformed
row is skipped without preventing valid rows from being crawled.

`source_status` is maintained manually from now on. Existing statuses and fields
such as `last_verification_date`, `final_resolved_url`, `failure_reason`, `evidence`
and provenance are **historical automated observations**, not human verification.
Retain them as reference and identify new human reviews using the optional reviewer
and manual-date fields. Historical files under `reports/sources/` are not a live
manual review queue; nothing regenerates them.

All selected valid-looking listing URLs are attempted by default. Verified is not
required. Mark an intentionally unusable URL `Invalid`, an intentionally excluded
company `Excluded`, or add `excluded=true`. These records are skipped. Missing
URLs and syntactically invalid URLs are also skipped. Blocked or Unverified
labels alone do not prevent an attempt; access restrictions during the crawl are
still respected. An explicit `source_status` spider argument can further restrict
a run, but it does not establish whether a review was manual.

## Identify missing sources without changing the file

```powershell
Import-Csv company_sources.csv | Where-Object { -not $_.job_listing_url } | Select-Object company_name,ticker
```

## Test your edits

```powershell
python -m scrapy crawl jobs -a only=NVDA -a max_jobs=10 -s JOB_OUTPUT_DIR=data/source-edit-test
```

Replace `NVDA` with the edited primary ticker. Check `crawl_summary.json`'s `sources`
section for selected/scheduled totals, missing/invalid/excluded/input-error counts,
and skipped company records. `crawl_failures.csv` contains crawl and item failures.
Research and edit corrections manually; the crawler never writes back to the CSV.

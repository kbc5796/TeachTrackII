"""
Figures out which job platform a career URL belongs to and builds the
JSON API addresses for the platforms that have one.

Run it by itself to see how many of your companies use each platform:

    python platforms.py company_sources.csv            # report only
    python platforms.py company_sources.csv --write    # also fill job_platform and sector
    python platforms.py company_sources.csv --sectors constituents.csv --write
                                                       # fill sector for all S&P 500 tickers
"""
import csv
import html
import re
import shutil
import sys
from collections import Counter
from urllib.parse import urlparse, parse_qs

LOCALE = re.compile(r"^[a-z]{2}([-_][A-Za-z]{2})?$")   # en, en-US, fr_CA

# Platforms we can read through a JSON API (fast, no browser needed).
API_PLATFORMS = {"Workday", "Greenhouse", "Lever"}


def detect_platform(url):
    host = urlparse(url or "").netloc.lower()
    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
        return "Workday"
    if host.endswith("greenhouse.io"):
        return "Greenhouse"
    if host.endswith("lever.co"):
        return "Lever"
    if "smartrecruiters.com" in host:
        return "SmartRecruiters"
    if "icims.com" in host:
        return "iCIMS"
    if "taleo.net" in host:
        return "Taleo"
    if "oraclecloud.com" in host:
        return "Oracle Cloud"
    if "successfactors" in host:
        return "SuccessFactors"
    if "eightfold.ai" in host:
        return "Eightfold"
    return "Custom"


def workday_api_base(url):
    """https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite
       -> https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite
    """
    p = urlparse(url)
    host = p.netloc.lower()
    tenant = host.split(".")[0]
    segs = [s for s in p.path.split("/") if s and not LOCALE.match(s)]
    if not segs:
        return None
    return f"https://{host}/wday/cxs/{tenant}/{segs[0]}"


def greenhouse_board(url):
    p = urlparse(url)
    qs = parse_qs(p.query)
    if "for" in qs:                      # boards.greenhouse.io/embed/job_board?for=xyz
        return qs["for"][0]
    segs = [s for s in p.path.split("/") if s and s != "embed"]
    return segs[0] if segs else None


def lever_company(url):
    segs = [s for s in urlparse(url).path.split("/") if s]
    return segs[0] if segs else None


def html_to_text(raw):
    """HTML (possibly entity-escaped, like Greenhouse) -> readable text
    that keeps line breaks, so section headings stay on their own lines."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h[1-6]>|</div>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


# ----------------------------------------------------------------------
# Sectors (GICS, the sector system the S&P 500 uses)
# ----------------------------------------------------------------------
# Built-in values for the starter companies. For all 500, download the
# S&P 500 constituents CSV once (it has "Symbol" and "GICS Sector" columns,
# e.g. https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv)
# and pass it with --sectors.
BUILTIN_SECTORS = {
    "MSFT": "Information Technology",
    "NVDA": "Information Technology",
    "CRM": "Information Technology",
    "ADBE": "Information Technology",
    "INTC": "Information Technology",
    "WMT": "Consumer Staples",
    "TGT": "Consumer Staples",
    "PFE": "Health Care",
    "MA": "Financials",
    "ABNB": "Consumer Discretionary",
    "AMZN": "Consumer Discretionary",
}


def _norm_ticker(t):
    return (t or "").strip().upper().replace(".", "-")


def load_sector_map(path=None):
    """ticker -> sector. Starts from the built-in list, then adds/overrides
    from a constituents CSV if one is given."""
    sectors = {_norm_ticker(k): v for k, v in BUILTIN_SECTORS.items()}
    if not path:
        return sectors
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames or []
        sym = next((c for c in cols if c.lower() in ("symbol", "ticker")), None)
        sec = next((c for c in cols
                    if "sector" in c.lower() and "sub" not in c.lower()), None)
        if not sym or not sec:
            raise SystemExit(
                f"{path} needs a Symbol/Ticker column and a GICS Sector column "
                f"(found: {cols})"
            )
        for row in reader:
            if row.get(sym) and row.get(sec):
                sectors[_norm_ticker(row[sym])] = row[sec].strip()
    return sectors


def page_text(response):
    """Visible text of a web page, without scripts, styles, menus, headers,
    footers, forms and icons (so saved text is the real page content and
    not CSS/JavaScript or navigation links)."""
    parts = response.xpath(
        "//body//text()[not(ancestor::script or ancestor::style "
        "or ancestor::noscript or ancestor::svg or ancestor::nav "
        "or ancestor::header or ancestor::footer or ancestor::template "
        "or ancestor::form or ancestor::select)]"
    ).getall()
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    path = args[0]
    write = "--write" in args
    sectors_file = None
    if "--sectors" in args:
        sectors_file = args[args.index("--sectors") + 1]

    sector_map = load_sector_map(sectors_file)

    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = list(reader)

    # Make sure the columns the spiders use exist.
    for col in ("sector", "website_url"):
        if col not in fields:
            fields.append(col)

    counts = Counter()
    missing_sector = []
    filled = 0
    for row in rows:
        url = (row.get("job_listing_url") or row.get("career_site_url") or "").strip()
        platform = detect_platform(url)
        counts[platform] += 1
        if write and platform != "Custom":
            row["job_platform"] = platform

        if not (row.get("sector") or "").strip():
            found = sector_map.get(_norm_ticker(row.get("ticker")))
            if found:
                row["sector"] = found
                filled += 1
            else:
                missing_sector.append(row.get("ticker") or row.get("company_name"))

    print(f"{len(rows)} companies")
    for platform, n in counts.most_common():
        tag = "API" if platform in API_PLATFORMS else "browser/custom"
        print(f"  {platform:<16}{n:>4}   ({tag})")
    print(f"Sectors: {filled} can be filled, {len(missing_sector)} still unknown"
          + (f" -> {', '.join(map(str, missing_sector[:15]))}" if missing_sector else ""))
    if missing_sector and not sectors_file:
        print("  Tip: add --sectors constituents.csv to fill the rest.")

    if write:
        shutil.copy(path, path + ".bak")
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        print(f"Updated {path} (backup: {path}.bak)")


if __name__ == "__main__":
    main()
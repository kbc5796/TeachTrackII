import csv
import hashlib
import io
import ipaddress
import json
import re
import socket
import threading
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from industry_crawler.spiders.platforms import (
    API_PLATFORMS,
    detect_platform,
    greenhouse_board,
    lever_company,
    workday_api_base,
)

FIELDS = [
    "company_name", "ticker", "career_site_url", "job_listing_url",
    "job_platform", "source_status", "sector", "website_url", "tickers",
    "cik", "last_verification_date", "final_resolved_url", "failure_reason",
    "evidence", "discovery_provenance", "constituent_source", "constituent_date",
]
STATUSES = ("Verified", "Unverified", "Needs Review", "Blocked", "Invalid",
            "No Listings Found")
REPO = "https://api.github.com/repos/datasets/s-and-p-500-companies"
WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def now():
    return datetime.now(UTC).isoformat()


def normalize_url(url):
    url = (url or "").strip()
    if not url or re.search(r"[\s\\\x00-\x1f]", url):
        raise ValueError("Missing URL or invalid URL characters")
    p = urlsplit(url)
    if p.scheme.lower() not in {"https", "http"} or not p.hostname:
        raise ValueError("Expected an absolute HTTP(S) URL")
    if p.username or p.password:
        raise ValueError("Credentials in URLs are forbidden")
    host = p.hostname.encode("idna").decode().lower()
    if host == "localhost" or "." not in host:
        raise ValueError("Expected a public hostname")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Nonpublic IP address")
    port = p.port
    if port not in {None, 80, 443}:
        raise ValueError("Nonstandard HTTP port")
    return urlunsplit((p.scheme.lower(), host + (f":{port}" if port else ""),
                       p.path or "/", p.query, ""))


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(FIELDS + [k for row in rows for k in row]))
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)


def company_key(row):
    cik = str(row.get("CIK") or row.get("cik") or "").lstrip("0")
    name = row.get("Security") or row.get("company_name") or ""
    name = re.sub(r"\s*\(class [^)]+\)", "", name, flags=re.IGNORECASE)
    return cik or re.sub(r"[^a-z0-9]", "", name.lower())


def import_companies(raw_rows, existing, metadata):
    old = {}
    for row in existing:
        for ticker in (row.get("tickers") or row.get("ticker", "")).split("|"):
            old[ticker.replace(".", "-").upper()] = row
    groups = {}
    for raw in raw_rows:
        if not raw.get("Symbol") or not raw.get("Security") or not raw.get("GICS Sector"):
            raise ValueError("Constituents require Symbol, Security and GICS Sector")
        key = company_key(raw)
        groups.setdefault(key, []).append(raw)
    result = []
    for group in groups.values():
        symbols = sorted({r["Symbol"].strip().upper() for r in group})
        seed = next((old[s.replace(".", "-")] for s in symbols
                     if s.replace(".", "-") in old), {})
        row = dict.fromkeys(FIELDS, "")
        row.update(seed)
        row.update(company_name=re.sub(r"\s*\(class [^)]+\)", "", group[0]["Security"],
                                       flags=re.IGNORECASE),
                   ticker=seed.get("ticker") or symbols[0], tickers="|".join(symbols),
                   sector=group[0]["GICS Sector"], cik=group[0].get("CIK", ""),
                   constituent_source=metadata["url"], constituent_date=metadata["date"])
        # Historical labels have no evidence from this validator.
        if not row.get("last_verification_date"):
            row.update(source_status="Unverified" if row["job_listing_url"] else "Needs Review",
                       failure_reason="Not yet validated" if row["job_listing_url"]
                       else "Missing official website and job source")
        result.append(row)
    return sorted(result, key=lambda r: r["company_name"].casefold())


class FetchError(Exception):
    def __init__(self, status, reason):
        self.status = status
        super().__init__(reason)


class Client:
    """Sequential, bounded requests; robots checked at every redirect origin."""
    def __init__(self, user_agent="TeachTrackSourceBot/1.0", delay=1, timeout=15,
                 session=None, resolver=socket.getaddrinfo):
        self.session = session or requests.Session()
        self.agent, self.delay, self.timeout = user_agent, delay, timeout
        self.resolver = resolver
        self.robots, self.last, self.cooldown = {}, {}, {}
        self.locks = {}
        self.lock_guard = threading.Lock()
        self.local = threading.local()
        self.custom_session = session

    def _request(self, url, method="GET", payload=None):
        origin = urlunsplit((*urlsplit(url)[:2], "", "", ""))
        with self.lock_guard:
            lock = self.locks.setdefault(origin, threading.RLock())
        with lock:
            return self._request_locked(url, method, payload)

    def _request_locked(self, url, method, payload):
        host = urlsplit(url).hostname
        addresses = self.resolver(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise FetchError("Invalid", "DNS resolves to nonpublic address")
        origin = urlunsplit((*urlsplit(url)[:2], "", "", ""))
        if origin in self.cooldown:
            raise FetchError("Blocked", "Origin previously returned HTTP 429; retry later")
        pause = max(self.delay, self.robots.get(origin, (None, 0))[1])
        time.sleep(max(0, pause - (time.monotonic() - self.last.get(origin, 0))))
        try:
            if not hasattr(self.local, "session"):
                self.local.session = self.custom_session or requests.Session()
            response = self.local.session.request(method, url, json=payload, timeout=self.timeout,
                                            allow_redirects=False,
                                            headers={"User-Agent": self.agent}, stream=True)
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 4_000_000:
                    raise FetchError("Needs Review", "Response exceeds 4 MB limit")
                chunks.append(chunk)
            response._content = b"".join(chunks)
            response.close()
        except requests.RequestException as exc:
            raise FetchError("Unverified", f"Network error: {type(exc).__name__}") from exc
        finally:
            self.last[origin] = time.monotonic()
        if response.status_code == 429:
            self.cooldown[origin] = True
        return response

    def _allowed(self, url):
        origin = urlunsplit((*urlsplit(url)[:2], "", "", ""))
        if origin not in self.robots:
            robots_url = origin + "/robots.txt"
            for _ in range(6):
                response = self._request(robots_url)
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("Location")
                if not location:
                    break
                robots_url = normalize_url(urljoin(robots_url, location))
            parser = RobotFileParser()
            if response.status_code in {404, 410}:
                parser.parse([])
            elif response.status_code == 200:
                parser.parse(response.text.splitlines())
            else:
                raise FetchError("Blocked", f"Robots policy unavailable: HTTP {response.status_code}")
            interval = parser.crawl_delay(self.agent) or parser.crawl_delay("*") or 0
            rate = parser.request_rate(self.agent) or parser.request_rate("*")
            if rate:
                interval = max(interval, rate.seconds / rate.requests)
            self.robots[origin] = parser, interval
        if not self.robots[origin][0].can_fetch(self.agent, url):
            raise FetchError("Blocked", "Disallowed by robots.txt")

    def get(self, url):
        for _ in range(6):
            try:
                url = normalize_url(url)
            except ValueError as exc:
                raise FetchError("Invalid", str(exc)) from exc
            self._allowed(url)
            response = self._request(url)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise FetchError("Invalid", "Redirect missing Location")
                target = urljoin(url, location)
                if urlsplit(url).scheme == "https" and urlsplit(target).scheme == "http":
                    raise FetchError("Needs Review", "HTTPS downgrade redirect")
                url = target
                continue
            response.url = url
            return response
        raise FetchError("Needs Review", "Redirect loop or more than five redirects")

    def post(self, url, payload):
        url = normalize_url(url)
        self._allowed(url)
        response = self._request(url, "POST", payload)
        response.url = url
        return response


def download_constituents(client, directory):
    response = client.get(REPO + "/commits?path=data/constituents.csv&per_page=1")
    response.raise_for_status()
    commit = response.json()[0]
    date = commit["commit"]["committer"]["date"]
    age = (datetime.now(UTC) - datetime.fromisoformat(date)).days
    if age > 14 or age < -1:
        raise ValueError(f"Upstream date {date} is stale or in the future; review before importing")
    url = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
           + commit["sha"] + "/data/constituents.csv")
    data = client.get(url)
    data.raise_for_status()
    raw = data.content
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if not 490 <= len(rows) <= 510:
        raise ValueError(f"Unexpected constituent count: {len(rows)}")
    metadata = {"url": url, "date": date, "retrieved_at": now(), "sha": commit["sha"],
                "sha256": hashlib.sha256(raw).hexdigest(), "ticker_count": len(rows),
                "currency": "Upstream updated within 14 days; not an S&P licensed feed"}
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "constituents.csv").write_bytes(raw)
    (directory / "provenance.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return rows, metadata


def links(page):
    soup = BeautifulSoup(page.text, "html.parser")
    for anchor in soup.select("a[href], iframe[src]"):
        raw = anchor.get("href") or anchor.get("src")
        try:
            yield normalize_url(urljoin(page.url, raw)), anchor.get_text(" ", strip=True)
        except ValueError:
            continue


def wikipedia_index(client):
    page = client.get(WIKI)
    page.raise_for_status()
    soup = BeautifulSoup(page.text, "html.parser")
    result = {}
    for tr in soup.select("#constituents tbody tr"):
        cells = tr.find_all("td")
        if len(cells) >= 2 and cells[1].find("a", href=True):
            result[cells[0].get_text(strip=True).replace(".", "-")] = urljoin(
                WIKI, cells[1].find("a", href=True)["href"])
    return result


def discover(row, client, wiki_urls=None, budget=8):
    """Follow observed career links only. Never synthesize company domains."""
    row = dict(row)
    errors = []
    if not row.get("website_url") and wiki_urls:
        article = wiki_urls.get(row["ticker"].replace(".", "-"))
        if article:
            try:
                page = client.get(article)
                soup = BeautifulSoup(page.text, "html.parser")
                for tr in soup.select("table.infobox tr"):
                    heading = tr.find("th")
                    if heading and heading.get_text(strip=True).lower() == "website":
                        anchor = tr.select_one("a.external[href]")
                        if anchor:
                            row["website_url"] = normalize_url(anchor["href"])
                            row["discovery_provenance"] = article
                            break
            except (FetchError, requests.RequestException, ValueError, OSError) as exc:
                errors.append(str(exc))
    queue = [u for u in (row.get("job_listing_url"), row.get("career_site_url"),
                        row.get("website_url")) if u]
    visited, best = set(), None
    for _ in range(budget):
        if not queue:
            break
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            page = client.get(url)
            candidate = validate_page(row, page)
            if url == row.get("job_listing_url") or candidate[0] == "Verified":
                best = page.url
            if candidate[1] == "Job links require accessible detail-page evidence" and not best:
                best = page.url
            if candidate[0] == "Verified" or candidate[1] == "Job links require accessible detail-page evidence":
                row["job_listing_url"] = page.url
                row["discovery_provenance"] = row.get("discovery_provenance", "") + f"; linked from {url}"
                break
            if page.status_code != 200 or candidate[0] == "Blocked":
                errors.append(f"{url}: {candidate[1]}")
                continue
            candidates = []
            for target, label in links(page):
                path = urlsplit(target).path.lower()
                if path.endswith((".mp4", ".pdf", ".png", ".jpg", ".svg", ".zip")):
                    continue
                host = urlsplit(target).hostname
                if host in {"www.linkedin.com", "www.facebook.com", "www.instagram.com", "www.youtube.com", "twitter.com", "x.com"}:
                    continue
                if re.search(r"career|jobs|vacanc|opportunit|open positions", host + path + " " + label, re.IGNORECASE):
                    if re.search(r"login|signin|register|subscribe|talentcommunity|privacy|terms|cookie", path, re.IGNORECASE):
                        continue
                    if not row.get("career_site_url"):
                        row["career_site_url"] = target
                    score = 2 if detect_platform(target) != "Custom" else 0
                    score += bool(re.search(r"search|jobs|positions", target + label, re.IGNORECASE))
                    score += 5 * bool(re.search(r"search jobs|find jobs|view.*jobs|search[-/]?jobs|job[-/]?search|/search", target + " " + label, re.IGNORECASE))
                    candidates.append((score, target))
                    direct = (detect_platform(target) != "Custom" and urlsplit(target).path != "/")
                    direct |= bool(re.search(r"search[-/]?jobs|job[-/]?search|jobs/search|careers/search", target, re.IGNORECASE))
                    if direct and not best and not listing_links(page):
                        best = target
                        row["discovery_provenance"] = row.get("discovery_provenance", "") + f"; {page.url} links to {target}"
            queue.extend(u for _, u in sorted(candidates, reverse=True) if u not in visited)
        except (FetchError, requests.RequestException, ValueError, OSError) as exc:
            errors.append(f"{url}: {exc}")
    if best and not row.get("job_listing_url"):
        row["job_listing_url"] = best
    row["failure_reason"] = "; ".join(errors)[:2000] or "Discovery complete; validation required"
    if not row.get("job_listing_url"):
        row["source_status"] = "Needs Review"
        row["failure_reason"] += "; No direct job-listing URL discovered"
    return row


def identity_matches(row, name):
    def tokens(value):
        return set(re.findall(r"[a-z0-9]+", str(value or "").lower())) - {
            "inc", "corporation", "corp", "company", "co", "the", "plc", "ltd", "group"}
    expected = tokens(row["company_name"])
    return bool(expected) and expected <= tokens(name)


def listing_links(page):
    return [(u, label) for u, label in links(page)
            if re.search(r"/(?:jobs?|positions?)/(?:[^/?]+/)*[^/?]+", urlsplit(u).path, re.IGNORECASE)
            and len(label.split()) >= 2
            and not re.search(r"search|view all|find jobs|browse|benefits|about|privacy|career", label, re.IGNORECASE)]


def validate_page(row, page):
    if page.status_code in {401, 403, 429}:
        return "Blocked", f"HTTP {page.status_code}", ""
    if page.status_code in {404, 410}:
        return "Invalid", f"HTTP {page.status_code}", ""
    if page.status_code != 200:
        return "Unverified", f"HTTP {page.status_code}", ""
    soup = BeautifulSoup(page.text, "html.parser")
    visible = soup.get_text(" ", strip=True)
    if re.search(r"verify you are human|access denied|captcha|sign in to continue", visible, re.IGNORECASE):
        return "Blocked", "Authentication or bot protection", ""
    postings = []
    def collect(obj):
        if isinstance(obj, list):
            for item in obj:
                collect(item)
        elif isinstance(obj, dict):
            types = obj.get("@type", [])
            if not isinstance(types, (str, list)):
                types = []
            if "JobPosting" in ([types] if isinstance(types, str) else types):
                postings.append(obj)
            collect(obj.get("@graph", []))
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            collect(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue
    valid = [p for p in postings if p.get("title") and p.get("description")
             and isinstance(p.get("hiringOrganization"), dict)
             and identity_matches(row, p["hiringOrganization"].get("name", ""))]
    # A board must expose links to individual jobs, not just a generic landing page.
    job_links = listing_links(page)
    brand = " ".join(e.get_text(" ", strip=True) for e in soup.select("title,h1"))
    if valid:
        return "Verified", "", f"{len(valid)} JobPosting records with matching hiringOrganization"
    if job_links and identity_matches(row, brand):
        return "Needs Review", "Job links require accessible detail-page evidence", ""
    if postings or job_links:
        return "Needs Review", "Listing evidence found but company identity not confirmed", ""
    if re.search(r"no (?:open |available )?(?:jobs|positions|vacancies)|0 jobs", visible, re.IGNORECASE):
        return "No Listings Found", "Page explicitly reports no listings", ""
    return "Unverified", "No accessible listing evidence; landing page or JavaScript shell", ""


def validate(row, client):
    row = dict(row)
    if not row.get("last_verification_date"):
        row.setdefault("discovery_error", row.get("failure_reason", ""))
    row.update(last_verification_date=now(), final_resolved_url="", evidence="")
    url = row.get("job_listing_url", "")
    if not url:
        row.update(source_status="Needs Review", failure_reason="Missing direct job-listing URL; " + row.get("discovery_error", ""))
        return row
    try:
        url = normalize_url(url)
        row["job_platform"] = detect_platform(url)
        if re.search(r"/(?:privacy|terms|cookies?)(?:/|$)", urlsplit(url).path, re.IGNORECASE):
            row.update(source_status="Needs Review", failure_reason="Observed candidate is a policy page, not a job board",
                       rejected_candidate_url=url, job_listing_url="")
            return row
        page = client.get(url)
        row["final_resolved_url"] = page.url
        row["job_platform"] = detect_platform(page.url, page.text)
        status, reason, evidence = validate_page(row, page)
        if reason == "Job links require accessible detail-page evidence":
            for detail_url, _ in listing_links(page)[:2]:
                detail = client.get(detail_url)
                if validate_page(row, detail)[0] == "Verified":
                    status, reason, evidence = "Verified", "", "Accessible company-matched JobPosting detail: " + detail.url
                    break
        if status in {"Unverified", "No Listings Found"} and row["job_platform"] in API_PLATFORMS:
            status, reason, evidence = validate_api(row, page.url, client)
        if status == "Unverified" and row["job_platform"] == "Custom":
            path = urlsplit(page.url).path.rstrip("/").lower()
            if path == "" or re.fullmatch(r"(?:/[a-z]{2}(?:-[a-z]{2})?){0,2}/careers?(?:/home)?", path):
                row.update(rejected_candidate_url=url, job_listing_url="")
                status, reason = "Needs Review", "Generic landing page lacks accessible listing evidence"
        row.update(source_status=status, failure_reason=reason, evidence=evidence)
    except ValueError as exc:
        row.update(source_status="Invalid", failure_reason=str(exc))
    except FetchError as exc:
        row.update(source_status=exc.status, failure_reason=str(exc))
    except (requests.RequestException, OSError) as exc:
        row.update(source_status="Unverified", failure_reason=f"Network error: {type(exc).__name__}")
    return row


def validate_api(row, board_url, client):
    """Read-only listing APIs; validate tenant identity and populated records."""
    platform = detect_platform(board_url)
    if platform == "Workday":
        base = workday_api_base(board_url)
        # myworkdaysite.com has a different path layout; do not guess an API.
        if not base or not urlsplit(board_url).hostname.endswith(".myworkdayjobs.com"):
            return "Needs Review", "Workday URL requires manual API mapping", ""
        tenant = urlsplit(board_url).hostname.split(".")[0]
        endpoint = base + "/jobs"
        page = client.post(endpoint, {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""})
    elif platform == "Greenhouse":
        tenant = greenhouse_board(board_url)
        if not tenant or not re.fullmatch(r"[A-Za-z0-9_-]+", tenant):
            return "Needs Review", "Missing or invalid Greenhouse board identifier", ""
        endpoint = f"https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs?content=true"
        page = client.get(endpoint)
    else:
        tenant = lever_company(board_url)
        if not tenant or not re.fullmatch(r"[A-Za-z0-9_-]+", tenant):
            return "Needs Review", "Missing or invalid Lever identifier", ""
        endpoint = f"https://api.lever.co/v0/postings/{tenant}?mode=json"
        page = client.get(endpoint)
    if page.status_code in {401, 403, 429}:
        return "Blocked", f"Listing API HTTP {page.status_code}", ""
    if page.status_code != 200:
        return "Unverified", f"Listing API HTTP {page.status_code}", ""
    try:
        data = page.json()
    except ValueError:
        return "Unverified", "Listing API returned non-JSON content", ""
    expected = re.sub(r"[^a-z0-9]", "", row["company_name"].lower())
    expected = re.sub(r"(?:corporation|incorporated|inc|corp|plc)$", "", expected)
    if tenant.lower() != expected:
        return "Needs Review", "API tenant identity needs manual confirmation", ""
    if platform == "Workday":
        jobs = data.get("jobPostings") if isinstance(data, dict) else None
        valid = [j for j in (jobs or []) if isinstance(j, dict) and j.get("title")
                 and str(j.get("externalPath", "")).startswith("/job/")]
    elif platform == "Greenhouse":
        jobs = data.get("jobs") if isinstance(data, dict) else None
        valid = [j for j in (jobs or []) if isinstance(j, dict) and j.get("title")
                 and j.get("id") and j.get("absolute_url") and j.get("content")]
    else:
        jobs = data if isinstance(data, list) else None
        valid = [j for j in (jobs or []) if isinstance(j, dict) and j.get("text")
                 and j.get("id") and j.get("hostedUrl") and j.get("description")]
    if valid:
        return "Verified", "", f"{len(valid)} populated {platform} API jobs; exact company tenant {tenant}; {endpoint}"
    if jobs == []:
        return "No Listings Found", "API reports empty listings", ""
    return "Unverified", "Listing API schema or records could not be confirmed", ""


def report(rows, directory):
    counts = Counter(r.get("source_status") or "Unverified" for r in rows)
    platforms = Counter(r.get("job_platform") or "Unknown" for r in rows)
    summary = {"generated_at": now(), "total_companies": len(rows),
               "unique_companies": len({company_key(r) for r in rows}),
               "ticker_count": sum(len((r.get("tickers") or r["ticker"]).split("|")) for r in rows),
               "companies_with_job_listing_urls": sum(bool(r.get("job_listing_url")) for r in rows),
               "status_counts": {s: counts[s] for s in STATUSES}, "platform_counts": dict(platforms),
               "unsupported_by_existing_json_crawler": sum(n for p, n in platforms.items()
                                                          if p not in API_PLATFORMS),
               "platforms_without_json_crawler_support": {p: n for p, n in platforms.items()
                                                          if p not in API_PLATFORMS},
               "manual_review_count": sum(r.get("source_status") != "Verified" for r in rows)}
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_csv(directory / "validation.csv", rows)
    write_csv(directory / "manual-review.csv", [r for r in rows if r.get("source_status") != "Verified"])
    return summary

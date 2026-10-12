import json

import pytest
import requests

from industry_crawler.sources.core import (
    Client,
    FetchError,
    discover,
    download_constituents,
    import_companies,
    normalize_url,
    read_csv,
    report,
    validate,
    validate_api,
    validate_page,
    write_csv,
)
from industry_crawler.spiders.platforms import detect_platform


def response(url="https://example.com/jobs", text="", status=200, headers=None):
    r = requests.Response()
    r.url, r.status_code = url, status
    r._content = text.encode()
    r.headers.update(headers or {})
    return r


ROW = {"company_name": "Example Corporation", "ticker": "EX", "cik": "12",
       "job_listing_url": "https://example.com/jobs"}


@pytest.mark.parametrize("url", ["", "example.com", "ftp://example.com", "http://localhost",
                                "https://127.0.0.1", "https://user:pass@example.com",
                                "https://example.com:bad", "https://example.com/a b"])
def test_invalid_url(url):
    with pytest.raises(ValueError):
        normalize_url(url)


def test_normalization():
    assert normalize_url(" HTTPS://Example.COM/jobs?q=a#top ") == "https://example.com/jobs?q=a"


@pytest.mark.parametrize("host,platform", [
    ("a.myworkdayjobs.com", "Workday"), ("a.myworkdaysite.com", "Workday"),
    ("boards.greenhouse.io", "Greenhouse"), ("jobs.lever.co", "Lever"),
    ("careers.smartrecruiters.com", "SmartRecruiters"), ("a.icims.com", "iCIMS"),
    ("a.taleo.net", "Taleo"), ("a.oraclecloud.com", "Oracle Cloud"),
    ("a.successfactors.eu", "SuccessFactors"), ("a.eightfold.ai", "Eightfold"),
    ("myworkdayjobs.com.evil.com", "Custom"), ("notlever.co", "Custom"),
])
def test_platform(host, platform):
    assert detect_platform("https://" + host) == platform


def test_embedded_platform():
    assert detect_platform("https://example.com", '<iframe src="https://jobs.lever.co/ex">') == "Lever"


def test_import_dedup_and_preserve():
    raw = [{"Symbol": s, "Security": "Example (Class " + s + ")",
            "GICS Sector": "IT", "CIK": "00012"} for s in ["EX", "EX.B"]]
    rows = import_companies(raw, [dict(ROW, extra="preserved")], {"url": "source", "date": "date"})
    assert len(rows) == 1
    assert rows[0]["tickers"] == "EX|EX.B"
    assert rows[0]["extra"] == "preserved"
    assert rows[0]["source_status"] == "Unverified"


def test_csv_roundtrip(tmp_path):
    path = tmp_path / "rows.csv"
    rows = [dict(ROW, company_name='Example, "International"', evidence="line1\nline2")]
    write_csv(path, rows)
    loaded = read_csv(path)[0]
    assert all(loaded[k] == v for k, v in rows[0].items())


@pytest.mark.parametrize("status,expected", [(403, "Blocked"), (429, "Blocked"),
                                           (404, "Invalid"), (503, "Unverified")])
def test_http_status(status, expected):
    assert validate_page(ROW, response(status=status))[0] == expected


@pytest.mark.parametrize("text,expected", [
    ("<title>Example careers</title><h1>Build your future</h1>", "Unverified"),
    ("<h1>Verify you are human</h1>", "Blocked"),
    ("No open positions", "No Listings Found"),
    ('<title>Other jobs</title><a href="/jobs/123">Software Engineer</a>', "Needs Review"),
    ('<title>Example jobs</title><a href="/jobs/123">Software Engineer</a>', "Needs Review"),
])
def test_listing_status(text, expected):
    assert validate_page(ROW, response(text=text))[0] == expected


def test_structured_listings():
    posting = {"@type": "JobPosting", "title": "Engineer", "description": "Build software",
               "hiringOrganization": {"name": "Example Corporation"}}
    page = response(text='<script type="application/ld+json">' + json.dumps({"@graph": [posting]}) + '</script>')
    assert validate_page(ROW, page)[0] == "Verified"
    assert validate_page(dict(ROW, company_name="Unrelated"), page)[0] == "Needs Review"


class FakeClient:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url):
        return self.pages[url]


def test_missing_source():
    assert validate(dict(ROW, job_listing_url=""), FakeClient({}))["source_status"] == "Needs Review"


def test_final_redirect_url():
    page = response(url="https://example.com/new", text="No open positions")
    row = validate(ROW, FakeClient({ROW["job_listing_url"]: page}))
    assert row["final_resolved_url"] == page.url
    assert row["last_verification_date"]


def test_discover_observed_links():
    home = "https://example.com/"
    board = "https://example.com/jobs"
    client = FakeClient({home: response(home, '<a href="/jobs">Open positions</a>'),
                         board: response(board, '<title>Example jobs</title><a href="/jobs/123">Software Engineer</a>')})
    row = discover(dict(ROW, website_url=home, job_listing_url=""), client)
    assert row["job_listing_url"] == board
    assert row["job_listing_url"] != home


def test_no_invented_url():
    row = discover(dict(ROW, job_listing_url=""), FakeClient({}))
    assert row["job_listing_url"] == ""
    assert row["source_status"] == "Needs Review"


class StubClient(Client):
    def __init__(self, pages):
        super().__init__(delay=0)
        self.pages, self.checked = pages, []

    def _allowed(self, url):
        self.checked.append(url)

    def _request(self, url):
        return self.pages[url]


def test_redirect_checks_each_destination():
    a, b = "https://example.com/", "https://other.com/"
    client = StubClient({a: response(a, status=302, headers={"Location": b}), b: response(b)})
    assert client.get(a).url == b
    assert client.checked == [a, b]


def test_redirect_private_address():
    a = "https://example.com/"
    client = StubClient({a: response(a, status=302, headers={"Location": "http://127.0.0.1/"})})
    with pytest.raises(FetchError):
        client.get(a)


def test_robots_denial():
    client = StubClient({"https://example.com/robots.txt": response(text="User-agent: *\nDisallow: /jobs")})
    with pytest.raises(FetchError, match="Disallowed"):
        Client._allowed(client, ROW["job_listing_url"])


def test_robots_unavailable_fails_closed():
    client = StubClient({"https://example.com/robots.txt": response(status=503)})
    with pytest.raises(FetchError, match="Robots policy unavailable"):
        Client._allowed(client, ROW["job_listing_url"])


def test_import_download_metadata(tmp_path):
    from industry_crawler.sources.core import REPO, now
    commit = {"sha": "abc", "commit": {"committer": {"date": now()}}}
    csv_text = "Symbol,Security,GICS Sector,CIK\n" + "EX,Example,IT,12\n" * 500
    client = FakeClient({REPO + "/commits?path=data/constituents.csv&per_page=1": response(text=json.dumps([commit])),
                         "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/abc/data/constituents.csv": response(text=csv_text)})
    rows, metadata = download_constituents(client, tmp_path)
    assert len(rows) == 500
    assert len(metadata["sha256"]) == 64
    assert (tmp_path / "provenance.json").exists()


def test_report(tmp_path):
    summary = report([dict(ROW, source_status="Needs Review")], tmp_path)
    assert summary["manual_review_count"] == 1
    assert summary["status_counts"]["Verified"] == 0
    assert (tmp_path / "manual-review.csv").exists()


def test_workday_api_requires_listings_and_identity():
    class APIClient:
        def post(self, url, payload):
            assert payload["limit"] == 20
            return response(url, json.dumps({"jobPostings": [{"title": "Engineer", "externalPath": "/job/123"}]}))
    row = dict(ROW, company_name="Example Corporation")
    board = "https://example.wd1.myworkdayjobs.com/External"
    assert validate_api(row, board, APIClient())[0] == "Verified"
    assert validate_api(dict(row, company_name="Other"), board, APIClient())[0] == "Needs Review"


def test_robots_redirect():
    a, b = "https://example.com/robots.txt", "https://www.example.com/robots.txt"
    client = StubClient({a: response(a, status=301, headers={"Location": b}),
                         b: response(b, "User-agent: *\nDisallow: /jobs")})
    with pytest.raises(FetchError, match="Disallowed"):
        Client._allowed(client, ROW["job_listing_url"])


def test_redirect_loop():
    a = "https://example.com/"
    client = StubClient({a: response(a, status=302, headers={"Location": a})})
    with pytest.raises(FetchError, match="Redirect loop"):
        client.get(a)


def test_job_links_must_resolve_to_listing_evidence():
    detail_url = "https://example.com/jobs/123"
    board = response(text='<title>Example jobs</title><a href="/jobs/123">Software Engineer</a>')
    posting = {"@type": "JobPosting", "title": "Engineer", "description": "Build software",
               "hiringOrganization": {"name": "Example"}}
    detail = response(detail_url, '<script type="application/ld+json">' + json.dumps(posting) + '</script>')
    client = FakeClient({ROW["job_listing_url"]: board, detail_url: detail})
    assert validate(ROW, client)["source_status"] == "Verified"
    client.pages[detail_url] = response(detail_url, "Careers information")
    assert validate(ROW, client)["source_status"] == "Needs Review"


@pytest.mark.parametrize("url", ["https://[bad", "https://example.com:bad", "not a url"])
def test_validator_invalid_urls(url):
    assert validate(dict(ROW, job_listing_url=url), FakeClient({}))["source_status"] == "Invalid"


def test_policy_page_is_not_a_job_board():
    row = validate(dict(ROW, job_listing_url="https://example.com/careers/search-jobs/privacy"), FakeClient({}))
    assert row["job_listing_url"] == ""
    assert row["source_status"] == "Needs Review"
    assert row["rejected_candidate_url"].endswith("/privacy")


@pytest.mark.parametrize("board,api,data", [
    ("https://boards.greenhouse.io/example", "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true",
     {"jobs": [{"title": "Engineer", "id": 123, "absolute_url": "https://boards.greenhouse.io/example/jobs/123", "content": "Build software"}]}),
    ("https://jobs.lever.co/example", "https://api.lever.co/v0/postings/example?mode=json",
     [{"text": "Engineer", "id": "abc", "hostedUrl": "https://jobs.lever.co/example/abc", "description": "Build software"}]),
])
def test_listing_apis(board, api, data):
    client = FakeClient({api: response(api, json.dumps(data))})
    assert validate_api(ROW, board, client)[0] == "Verified"
    client.pages[api] = response(api, "{}")
    assert validate_api(ROW, board, client)[0] == "Unverified"
    client.pages[api] = response(api, status=403)
    assert validate_api(ROW, board, client)[0] == "Blocked"


def test_nonpublic_dns_rejected():
    resolver = lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))]
    client = Client(resolver=resolver)
    with pytest.raises(FetchError, match="nonpublic"):
        client._request("https://example.com/")


def test_rate_limit_suspends_origin(monkeypatch):
    monkeypatch.setattr("industry_crawler.sources.core.time.sleep", lambda delay: None)
    r = response(status=429)
    r.iter_content = lambda size: iter([b"Rate limited"])
    r.close = lambda: None
    class Session:
        def request(self, *args, **kwargs):
            return r
    resolver = lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))]
    client = Client(session=Session(), resolver=resolver)
    assert client._request("https://example.com/").status_code == 429
    with pytest.raises(FetchError, match="previously returned HTTP 429"):
        client._request("https://example.com/jobs")


def test_generic_landing_candidate_requires_review():
    url = "https://example.com/careers"
    row = validate(dict(ROW, job_listing_url=url), FakeClient({url: response(url, "Join Example") }))
    assert row["source_status"] == "Needs Review"
    assert row["job_listing_url"] == ""
    assert row["rejected_candidate_url"] == url


def test_offline_import_checks_hash(tmp_path, monkeypatch):
    from industry_crawler.sources.__main__ import main
    (tmp_path / "constituents.csv").write_text("Symbol,Security,GICS Sector\nEX,Example,IT\n")
    (tmp_path / "provenance.json").write_text(json.dumps({"url": "saved", "date": "date", "sha256": "wrong"}))
    monkeypatch.setattr("sys.argv", ["sources", "import", "--offline", "--data-dir", str(tmp_path),
                                     "--csv", str(tmp_path / "companies.csv")])
    with pytest.raises(ValueError, match="provenance hash"):
        main()
    assert not (tmp_path / "companies.csv").exists()

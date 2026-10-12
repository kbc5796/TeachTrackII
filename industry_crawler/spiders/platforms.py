"""Job platform detection, extraction API helpers and HTML text utilities."""
import html
import re
from urllib.parse import parse_qs, urlparse

LOCALE = re.compile(r"^[a-z]{2}([-_][A-Za-z]{2})?$")   # en, en-US, fr_CA

# Platforms we can read through a JSON API (fast, no browser needed).
API_PLATFORMS = {"Workday", "Greenhouse", "Lever"}


def detect_platform(url, content=""):
    """Detect known hosts or embedded vendor URLs; retain crawler labels."""
    host = (urlparse(url or "").hostname or "").lower()
    def belongs(domain):
        return host == domain or host.endswith("." + domain)

    if belongs("myworkdayjobs.com") or belongs("myworkdaysite.com"):
        return "Workday"
    if belongs("greenhouse.io") or belongs("greenhouse.com"):
        return "Greenhouse"
    if belongs("lever.co"):
        return "Lever"
    if belongs("smartrecruiters.com"):
        return "SmartRecruiters"
    if belongs("icims.com"):
        return "iCIMS"
    if belongs("taleo.net"):
        return "Taleo"
    if belongs("oraclecloud.com"):
        return "Oracle Cloud"
    if belongs("successfactors.com") or belongs("successfactors.eu"):
        return "SuccessFactors"
    if belongs("eightfold.ai"):
        return "Eightfold"
    for embedded in re.findall(r'https?://[^\s\"\'<>]+', content):
        platform = detect_platform(embedded)
        if platform != "Custom":
            return platform
    return "Custom"


def workday_api_base(url):
    """https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite
       -> https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite
    """
    p = urlparse(url)
    host = p.netloc.lower()
    tenant = host.split(".")[0]
    segs = [s for s in p.path.split("/") if s and not LOCALE.match(s)]
    if host.endswith(".myworkdaysite.com") and "recruiting" in segs:
        segs = segs[segs.index("recruiting") + 1:]
        if len(segs) < 2:
            return None
        tenant, segs = segs[0], segs[1:]
    if not segs:
        return None
    return f"https://{host}/wday/cxs/{tenant}/{segs[0]}"


def workday_search_filters(url):
    """Carry supplied Workday facet IDs into list API requests, without guessing."""
    query = parse_qs(urlparse(url).query)
    facets = {key: values for key, values in query.items()
              if key in {"locationCountry", "Location_Country", "Country", "locations",
                         "locationHierarchy1", "locationHierarchy2", "jobFamilyGroup",
                         "CF_-_REC_-_LRV_-_Job_Posting_Anchor_-_Country_from_Job_Posting_Location_Extended",
                         "jobFamily", "timeType", "workerSubType", "workerType"}}
    return facets, query.get("q", [""])[0]


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

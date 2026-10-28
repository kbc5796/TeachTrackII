"""Conservative URL normalization and company-scoped identity."""
import hashlib
import json
import re
import ipaddress
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def canonical_url(url):
    try:
        p = urlsplit(url.strip())
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            return ""
        if any(c.isspace() for c in p.netloc) or p.port == 0:
            return ""
        host = p.hostname.lower()
        if host == "localhost" or host.endswith((".localhost", ".local")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        if p.port not in {None, 80, 443}:
            return ""
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in {"source", "ref"}]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/",
                           urlencode(sorted(query)), ""))
    except (ValueError, AttributeError):
        return ""


def job_identity(item):
    company = (item.get("ticker") or item.get("company_name") or "").strip().casefold()
    job_id = str(item.get("job_id") or "").strip()
    if job_id:
        identity = (company, item.get("job_platform", "").casefold(), "id", job_id)
    elif canonical_url(item.get("job_url", "")):
        identity = (company, "url", canonical_url(item["job_url"]))
    else:
        identity = (company, "fallback", " ".join(item.get("job_title", "").lower().split()),
                    " ".join(item.get("location", "").lower().split()))
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def clean_text(value):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def company_identity(item):
    ticker = clean_text(item.get("ticker")).upper().replace(".", "-")
    return "ticker:" + ticker if ticker else "name:" + clean_text(item.get("company_name")).casefold()


# Full names are changed only as comma-delimited location components.
STATES = dict(pair.split(":") for pair in (
    "alabama:AL|alaska:AK|arizona:AZ|arkansas:AR|california:CA|colorado:CO|connecticut:CT|"
    "delaware:DE|florida:FL|georgia:GA|hawaii:HI|idaho:ID|illinois:IL|indiana:IN|iowa:IA|"
    "kansas:KS|kentucky:KY|louisiana:LA|maine:ME|maryland:MD|massachusetts:MA|michigan:MI|"
    "minnesota:MN|mississippi:MS|missouri:MO|montana:MT|nebraska:NE|nevada:NV|"
    "new hampshire:NH|new jersey:NJ|new mexico:NM|new york:NY|north carolina:NC|"
    "north dakota:ND|ohio:OH|oklahoma:OK|oregon:OR|pennsylvania:PA|rhode island:RI|"
    "south carolina:SC|south dakota:SD|tennessee:TN|texas:TX|utah:UT|vermont:VT|"
    "virginia:VA|washington:WA|west virginia:WV|wisconsin:WI|wyoming:WY|district of columbia:DC"
).split("|"))


def normalize_location(value):
    text = clean_text(value)
    if re.search(r",\s*(?:Canada|Australia|United Kingdom|UK)\b", text, re.I):
        return text
    parts = re.split(r"(,|;)", text)
    for i in range(2, len(parts), 2):
        part = parts[i].strip()
        parts[i] = " " + STATES.get(part.casefold(), part.upper() if part.upper() in STATES.values() else part)
    return "".join(parts)


def employment_type(value):
    if isinstance(value, list):
        return ", ".join(dict.fromkeys(filter(None, (employment_type(v) for v in value))))
    text = clean_text(value)
    key = re.sub(r"[\s_-]+", "", text).casefold()
    return {"fulltime": "Full-time", "parttime": "Part-time", "contract": "Contract",
            "contractor": "Contract", "temporary": "Temporary", "intern": "Internship",
            "internship": "Internship", "permanent": "Permanent", "volunteer": "Volunteer",
            "other": "Other"}.get(key, text)


def normalize_job(item):
    result = dict(item)
    raw = dict(item.get("original_values") or {})
    converters = {"ticker": lambda v: clean_text(v).upper().replace(".", "-"),
                  "company_name": clean_text, "job_title": clean_text, "location": normalize_location,
                  "job_url": canonical_url, "job_id": clean_text, "employment_type": employment_type}
    for field, convert in converters.items():
        value = item.get(field, "")
        normalized = convert(value)
        if value != normalized:
            raw.setdefault(field, value)
        result[field] = normalized
    for field in ("description", "responsibilities", "requirements"):
        if field in result and not isinstance(result[field], str):
            raw.setdefault(field, result[field])
            result[field] = ""
    result["company_id"] = company_identity(result)
    result["source_platform"] = clean_text(item.get("source_platform") or item.get("job_platform"))
    result["source_job_id"] = clean_text(item.get("source_job_id") or result["job_id"])
    result["requisition_id"] = clean_text(item.get("requisition_id"))
    if raw:
        result["original_values"] = raw
    return result

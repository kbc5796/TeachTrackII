"""Conservative URL normalization and company-scoped identity."""
import hashlib
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def canonical_url(url):
    try:
        p = urlsplit(url.strip())
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            return ""
        if any(c.isspace() for c in p.netloc) or p.port == 0:
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

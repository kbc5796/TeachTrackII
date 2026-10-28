"""Restrict job requests (including redirects) to the approved source/ATS hosts."""
from urllib.parse import urlsplit

from scrapy.exceptions import IgnoreRequest

from .normalization import canonical_url


class SourcePolicyMiddleware:
    def process_request(self, request, spider):
        if spider.name != "jobs":
            return None
        row = request.cb_kwargs.get("row", {})
        hosts = {urlsplit(canonical_url(row.get(field, ""))).hostname
                 for field in ("job_listing_url",)}
        hosts.update({"boards-api.greenhouse.io", "api.lever.co", "api.smartrecruiters.com"})
        if request.meta.get("dont_obey_robotstxt"):
            # Robots requests have no row context. Limit them to repository sources
            # and the fixed API hosts, including their redirects.
            from .company_input import read_companies
            from .spiders.jobs import CSV_FILE
            hosts.update(urlsplit(canonical_url(r.get("job_listing_url", ""))).hostname
                         for r in read_companies(CSV_FILE))
        if not canonical_url(request.url) or urlsplit(request.url).hostname not in hosts:
            raise IgnoreRequest("Unsafe or unapproved job request host")
        return None

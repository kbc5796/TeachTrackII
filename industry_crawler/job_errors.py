"""Quarantine API decode and extraction failures with source context."""


class JobErrorMiddleware:
    def process_spider_exception(self, response, exception, spider):
        row = response.request.cb_kwargs.get("row", {})
        return [{"record_type": "skipped", "company_name": row.get("company_name", ""),
                 "ticker": row.get("ticker", ""), "source_url": response.url,
                 "reason": type(exception).__name__, "detail": str(exception)[:200]}]

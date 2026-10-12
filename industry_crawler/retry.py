"""Nonblocking exponential waits before Scrapy's bounded retry requests."""
from twisted.internet import task


class BackoffMiddleware:
    def process_request(self, request, spider):
        attempts = request.meta.get("retry_times", 0)
        if attempts:
            from twisted.internet import reactor
            return task.deferLater(reactor, min(30, 2 ** attempts), lambda: None)

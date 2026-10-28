"""Adapters share spider limits, analysis, errors and the JobRecord factory."""
from abc import ABC, abstractmethod


class JobAdapter(ABC):
    def __init__(self, spider):
        self.spider = spider

    def __getattr__(self, name):
        return getattr(self.spider, name)

    @abstractmethod
    def start(self, row, url, platform):
        """Return the first Scrapy request, or None for HTML fallback."""

    def data(self, response, kind=dict):
        data = response.json()
        if not isinstance(data, kind) or (isinstance(data, dict) and ('error' in data or 'errors' in data)):
            raise ValueError("Malformed or unsuccessful API response")
        return data

    @staticmethod
    def records(data, field):
        records = data.get(field)
        if not isinstance(records, list):
            raise ValueError(f"Missing or malformed {field} list")
        return records

    @staticmethod
    def mapping(value):
        return value if isinstance(value, dict) else {}

    def malformed(self, response, row):
        return {"record_type": "skipped", **row, "source_url": response.url,
                "reason": "malformed API job"}

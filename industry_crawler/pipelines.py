"""Validate, filter and store individual job postings."""
from scrapy.exceptions import DropItem

from .cs_filter import score_job
from .normalization import canonical_url
from .storage import JobStore


class JobPipeline:
    @classmethod
    def from_crawler(cls, crawler):
        instance = cls()
        instance.crawler = crawler
        return instance

    def open_spider(self, spider):
        self.store = JobStore(self.crawler.settings.get("JOB_OUTPUT_DIR", "data"))

    def process_item(self, item, spider):
        if item.get("record_type") != "job":
            self.store.reject(item, item.get("reason", item.get("record_type", "invalid")))
            raise DropItem("Not an individual job", log_level="DEBUG")
        item["job_url"] = canonical_url(item.get("job_url", ""))
        missing = [k for k in ("company_name", "job_title", "description", "job_url") if not item.get(k)]
        if missing:
            self.store.reject(item, "missing/invalid: " + ", ".join(missing))
            raise DropItem("Invalid job", log_level="DEBUG")
        item.update(score_job(item["job_title"], item["description"],
                              item.get("responsibilities", ""), item.get("requirements", ""),
                              float(self.crawler.settings.get("CS_RELEVANCE_THRESHOLD", 0.6))))
        if not item["is_cs_job"]:
            self.store.reject(item, "non-CS: " + item["cs_filter_reason"])
            raise DropItem("Non-CS job", log_level="DEBUG")
        if not self.store.save(item):
            self.crawler.stats.inc_value("jobs/duplicate_updates")
            raise DropItem("Already stored; refreshed in SQLite", log_level="DEBUG")
        self.crawler.stats.inc_value("jobs/accepted")
        return item

    def close_spider(self, spider):
        self.store.export({**self.crawler.stats.get_stats(),
                           "sources": getattr(spider, "source_summary", {})})
        self.store.close()

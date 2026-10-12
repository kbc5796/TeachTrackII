"""Offline replay of clearly labeled captured records; never a fresh live crawl."""
import argparse
import csv
import json
from pathlib import Path
from types import SimpleNamespace

from scrapy.exceptions import DropItem

from .pipelines import JobPipeline
from .spiders.skill_categories import analyze_job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", default="company_sources.csv")
    parser.add_argument("--fixtures", default="tests/fixtures/captured_jobs.json")
    parser.add_argument("--output", default="data/fixture-demo")
    args = parser.parse_args()
    with open(args.sources, encoding="utf-8-sig", newline="") as source:
        companies = {r["ticker"]: r for r in csv.DictReader(source)}
    records = json.loads(Path(args.fixtures).read_text(encoding="utf-8"))
    stats = {}
    def increment(key):
        stats[key] = stats.get(key, 0) + 1
    pipeline = JobPipeline()
    pipeline.crawler = SimpleNamespace(settings={"JOB_OUTPUT_DIR": args.output},
                                       stats=SimpleNamespace(inc_value=increment, get_stats=lambda: stats))
    pipeline.open_spider(None)
    for record in records + records[:1]:
        if record.get("ticker") not in companies:
            continue
        item = {**record, **analyze_job(record["job_title"], record["description"]),
                "data_origin": "captured live snapshot replay; not a current crawl"}
        try:
            pipeline.process_item(item, None)
        except DropItem:
            pass
    stats["demo_mode"] = "captured fixture replay"
    pipeline.close_spider(None)
    print(json.dumps(stats))


if __name__ == "__main__":
    main()

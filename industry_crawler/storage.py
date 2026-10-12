"""Transactional incremental storage and atomic UTF-8 exports."""
import csv
import io
import json
import os
import sqlite3
from pathlib import Path

from .normalization import job_identity


class JobStore:
    def __init__(self, directory="data"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "jobs.sqlite3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (identity TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS failures (payload TEXT NOT NULL)")
        self.db.execute("CREATE INDEX IF NOT EXISTS jobs_company_url ON jobs "
                        "(json_extract(payload, '$.ticker'), json_extract(payload, '$.job_url'))")

    def save(self, item):
        key = job_identity(item)
        merge_key = None
        old = self.db.execute("SELECT 1 FROM jobs WHERE identity=?", (key,)).fetchone()
        # Merge an ID-less capture with a known posting at the same URL, while
        # preserving two different platform IDs even if a board reuses a URL.
        if not old and item.get("job_url"):
            candidates = self.db.execute(
                "SELECT identity, payload FROM jobs WHERE json_extract(payload, '$.ticker')=? "
                "AND json_extract(payload, '$.job_url')=?",
                (item.get("ticker", ""), item["job_url"])).fetchall()
            for old_key, payload in candidates:
                previous = json.loads(payload)
                if (previous.get("company_name") != item.get("company_name")
                        or previous.get("job_platform") != item.get("job_platform")):
                    continue
                if not previous.get("job_id") or not item.get("job_id"):
                    if not item.get("job_id") and previous.get("job_id"):
                        item = {**item, "job_id": previous["job_id"]}
                        key = job_identity(item)
                    merge_key = old_key
                    old = True
                    break
        with self.db:
            if merge_key:
                self.db.execute("DELETE FROM jobs WHERE identity=?", (merge_key,))
            self.db.execute("INSERT INTO jobs VALUES (?, ?) ON CONFLICT(identity) "
                            "DO UPDATE SET payload=excluded.payload", (key, json.dumps(item)))
        return not bool(old)

    def reject(self, item, reason):
        with self.db:
            self.db.execute("INSERT INTO failures VALUES (?)", (json.dumps({**item, "reason": reason}),))

    def _write(self, name, content):
        target = self.directory / name
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)

    def export(self, summary=None):
        jobs = [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM jobs ORDER BY identity")]
        failures = [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM failures")]
        self._write("cs_jobs.json", json.dumps(jobs, indent=2, ensure_ascii=False))
        self._write("cs_jobs.jsonl", "".join(json.dumps(j, ensure_ascii=False) + "\n" for j in jobs))
        for name, records, defaults in [("cs_jobs.csv", jobs, ["record_type", "job_title", "job_url"]),
                                        ("crawl_failures.csv", failures, ["source_url", "reason"])]:
            output = io.StringIO(newline="")
            fields = sorted(set().union(*(r.keys() for r in records))) if records else defaults
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for record in records:
                writer.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v
                                 for k, v in record.items()})
            self._write(name, output.getvalue())
        self._write("crawl_summary.json", json.dumps({"stored_jobs": len(jobs),
                    "recorded_failures": len(failures), **(summary or {})}, indent=2, default=str))

    def close(self):
        self.db.close()

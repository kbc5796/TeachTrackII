"""Transactional incremental storage and atomic UTF-8 exports."""
import csv
import io
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

from .normalization import job_identity, normalize_job, company_identity


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

        # Additive index only: existing payloads and primary keys remain untouched.
        self.db.execute("CREATE TABLE IF NOT EXISTS job_companies (identity TEXT PRIMARY KEY, company TEXT NOT NULL)")
        self.db.execute("CREATE INDEX IF NOT EXISTS job_company ON job_companies(company)")
        with self.db:
            for key, payload in self.db.execute(
                    "SELECT identity, payload FROM jobs WHERE identity NOT IN (SELECT identity FROM job_companies)"):
                self.db.execute("INSERT INTO job_companies VALUES (?, ?)", (key, company_identity(json.loads(payload))))

    @staticmethod
    def observation(item):
        fields = ("source_platform", "source_job_id", "job_platform", "job_id", "requisition_id",
                  "job_url", "source_url", "job_board_url", "company_name", "source_employer",
                  "original_values")
        return {k: item[k] for k in fields if k in item}

    def save(self, item):
        item = normalize_job(item)
        company = company_identity(item)
        platform = item["source_platform"].casefold()
        source_id = item["source_job_id"]
        url = item["job_url"]
        req = item["requisition_id"]
        matches, uncertain = {}, set()
        candidates = self.db.execute(
            "SELECT DISTINCT j.identity, j.payload FROM jobs j JOIN job_companies c "
            "ON j.identity=c.identity WHERE c.company=?", (company,)).fetchall()
        for old_key, payload in candidates:
            previous = normalize_job(json.loads(payload))
            observations = previous.get("source_observations") or [self.observation(previous)]
            evidence = 0
            conflict = False
            same_url = False
            for obs in observations:
                old_platform = (obs.get("source_platform") or obs.get("job_platform") or "").casefold()
                old_id = str(obs.get("source_job_id") or obs.get("job_id") or "")
                same_platform = platform == old_platform
                same_url |= bool(url and url == obs.get("job_url"))
                # Conflicting IDs on one platform override a reused URL or requisition.
                conflict |= bool(same_platform and source_id and old_id and source_id != old_id)
                if same_platform and source_id and source_id == old_id:
                    evidence = max(evidence, 4)
                conflict |= bool(req and obs.get("requisition_id") and req != obs["requisition_id"])
                if req and req == obs.get("requisition_id"):
                    evidence = max(evidence, 3)
                if url and url == obs.get("job_url") and (not source_id or not old_id):
                    evidence = max(evidence, 2 if same_platform and not source_id and not old_id else 1)
            if evidence and not conflict:
                matches[old_key] = evidence
            elif same_url or (item.get("job_title") and item["job_title"].casefold() ==
                              previous.get("job_title", "").casefold() and
                              item["location"].casefold() == previous["location"].casefold()):
                uncertain.add(old_key)
        # Ambiguous URL/ID-less observations never select an arbitrary candidate.
        best = {key for key, rank in matches.items() if rank == max(matches.values())} if matches else set()
        old_key = next(iter(best)) if len(best) == 1 else None
        uncertain.update(set(matches) - ({old_key} if old_key else set()))
        now = item.get("crawl_date") or datetime.now(timezone.utc).isoformat()
        observation = self.observation(item)
        if old_key:
            previous = json.loads(self.db.execute("SELECT payload FROM jobs WHERE identity=?", (old_key,)).fetchone()[0])
            observations = previous.get("source_observations") or [self.observation(normalize_job(previous))]
            if observation not in observations:
                observations.append(observation)
            # Retain useful known values when an observation has a missing field.
            item = {**previous, **{k: v for k, v in item.items() if v not in ("", None, [], {})}}
            if not source_id and (previous.get("source_job_id") or previous.get("job_id")):
                for field in ("job_id", "source_job_id", "job_platform", "source_platform"):
                    item[field] = previous.get(field, "")
            item["first_seen"] = previous.get("first_seen") or previous.get("crawl_date") or now
            item["last_seen"] = max(previous.get("last_seen") or item["first_seen"], now)
            item["source_observations"] = observations
            key = old_key
        else:
            key = job_identity(item)
            if (not source_id and not url) or self.db.execute("SELECT 1 FROM jobs WHERE identity=?", (key,)).fetchone():
                key = uuid4().hex
            item["first_seen"] = item["last_seen"] = now
            item["source_observations"] = [observation]
        if uncertain:
            item["duplicate_review"] = sorted(set(item.get("duplicate_review", [])) | uncertain)
        with self.db:
            self.db.execute("INSERT INTO jobs VALUES (?, ?) ON CONFLICT(identity) "
                            "DO UPDATE SET payload=excluded.payload", (key, json.dumps(item)))
            self.db.execute("INSERT OR IGNORE INTO job_companies VALUES (?, ?)", (key, company))
        return old_key is None

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

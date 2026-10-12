import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from requests import RequestException

from .core import (
    Client,
    FetchError,
    discover,
    download_constituents,
    import_companies,
    read_csv,
    report,
    validate,
    wikipedia_index,
    write_csv,
)


def main():
    parser = argparse.ArgumentParser(description="TeachTrack S&P 500 source collection")
    parser.add_argument("command", choices=["import", "discover", "validate", "report"])
    parser.add_argument("--csv", default="company_sources.csv")
    parser.add_argument("--data-dir", default="data/sp500")
    parser.add_argument("--report-dir", default="reports/sources")
    parser.add_argument("--offline", action="store_true", help="Import saved constituent snapshot")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only", help="Comma-separated tickers")
    parser.add_argument("--user-agent", default="TeachTrackSourceBot/1.0")
    parser.add_argument("--delay", type=float, default=1)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--workers", type=int, default=1,
                        help="Parallel companies; requests remain serial and delayed per origin")
    args = parser.parse_args()
    if args.delay < 1 or args.timeout <= 0 or not 1 <= args.workers <= 8 or (args.limit is not None and args.limit < 1):
        parser.error("delay must be >= 1; timeout and limit must be positive; workers must be 1..8")
    client = Client(args.user_agent, args.delay, args.timeout)
    if args.command != "import" and not Path(args.csv).exists():
        parser.error(f"Company CSV does not exist: {args.csv}; run import first")
    rows = read_csv(args.csv) if Path(args.csv).exists() else []
    if args.command == "import":
        if args.offline:
            raw = read_csv(Path(args.data_dir) / "constituents.csv")
            metadata = json.loads((Path(args.data_dir) / "provenance.json").read_text(encoding="utf-8"))
            digest = hashlib.sha256((Path(args.data_dir) / "constituents.csv").read_bytes()).hexdigest()
            if digest != metadata["sha256"]:
                raise ValueError("Saved constituent snapshot does not match its provenance hash")
        else:
            raw, metadata = download_constituents(client, args.data_dir)
        rows = import_companies(raw, rows, metadata)
        write_csv(args.csv, rows)
    elif args.command in {"discover", "validate"}:
        wiki = None
        if args.command == "discover":
            try:
                wiki = wikipedia_index(client)
            except (FetchError, OSError, ValueError, RequestException) as exc:
                print(f"Wikipedia index unavailable: {exc}; using existing seeds")
        selected = {t.strip().upper().replace(".", "-") for t in args.only.split(",")} if args.only else None
        indices = []
        for i, row in enumerate(rows):
            symbols = {t.replace(".", "-") for t in (row.get("tickers") or row["ticker"]).upper().split("|")}
            if selected and not symbols & selected:
                continue
            if args.limit and len(indices) >= args.limit:
                break
            indices.append(i)
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(discover, rows[i], client, wiki) if args.command == "discover"
                       else executor.submit(validate, rows[i], client): i for i in indices}
            for processed, future in enumerate(as_completed(futures), 1):
                i = futures[future]
                rows[i] = future.result()
                write_csv(args.csv, rows)  # checkpoint every company for interrupted runs
                print(f"{processed}: {rows[i]['ticker']} {rows[i].get('source_status')}", flush=True)
    print(json.dumps(report(rows, args.report_dir), indent=2))


if __name__ == "__main__":
    main()

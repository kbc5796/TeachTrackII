"""Read manually maintained company CSVs without changing or fetching sources."""
import csv

REQUIRED_COLUMNS = {"company_name", "ticker", "job_listing_url"}


def read_companies(path):
    with open(path, encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = reader.fieldnames or []
        missing = REQUIRED_COLUMNS - set(fields)
        if missing or len(fields) != len(set(fields)):
            raise ValueError("Invalid company CSV header; required unique columns: "
                             + ", ".join(sorted(REQUIRED_COLUMNS)))
        for number, row in enumerate(reader, 2):
            malformed = None in row or any(value is None for value in row.values())
            # Retain optional manual review columns.
            row = {key: value or "" for key, value in row.items() if key is not None}
            if malformed:
                row["input_error"] = f"Malformed CSV row {number}: column count mismatch"
            yield row

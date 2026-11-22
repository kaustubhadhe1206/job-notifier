"""Build api/_core/data/h1b_sponsors.json.gz from the USCIS H-1B Employer Data Hub.

Usage:  python scripts/build_h1b_data.py [YEAR ...]          (default: 2021 2022 2023)
        python scripts/build_h1b_data.py path/to/h1b_datahubexport-2024.csv ...

If USCIS blocks the automatic download (HTTP 403), download the CSVs in your browser from
the page below and pass the file paths instead (the year is read from the file).

The CSVs list every employer with H-1B petitions approved in a fiscal year. "Initial
approvals" are new H-1B employment, including F-1/OPT students changing status, which is
the number that matters most for you. Re-run when USCIS publishes a newer year at
https://www.uscis.gov/archive/h-1b-employer-data-hub-files
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import os
import sys

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "api"))
from _core.eligibility import DATA_PATH, normalize_company  # noqa: E402

URL = "https://www.uscis.gov/sites/default/files/document/data/h1b_datahubexport-{year}.csv"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def _int(v: str) -> int:
    try:
        return int(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return 0


HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.uscis.gov/archive/h-1b-employer-data-hub-files",
}


def _load(source: str) -> bytes:
    if os.path.exists(source):
        with open(source, "rb") as f:
            return f.read()
    url = URL.format(year=source)
    print(f"Downloading {url}", flush=True)
    r = httpx.get(url, headers=HEADERS, timeout=120, follow_redirects=True)
    if r.status_code == 403:
        sys.exit(f"USCIS refused the download (403). Open {url} in your browser, save the file, "
                 f"and run: python scripts/build_h1b_data.py <saved file paths>")
    r.raise_for_status()
    return r.content


def main(sources: list[str]) -> None:
    agg: dict[str, list] = {}  # key -> [initial, continuing, last_fy, {legal name: approvals}]
    years: set[int] = set()
    for source in sources:
        rows = csv.DictReader(io.StringIO(_load(source).decode("utf-8-sig", errors="replace")))
        n = 0
        for row in rows:
            year = _int(row.get("Fiscal Year"))
            years.add(year)
            employer = (row.get("Employer") or row.get("Employer (Petitioner) Name") or "").strip()
            if not employer:
                continue
            initial = _int(row.get("Initial Approval") or row.get("Initial Approvals"))
            continuing = _int(row.get("Continuing Approval") or row.get("Continuing Approvals"))
            if initial + continuing == 0:
                continue
            names = [employer]
            upper = employer.upper()
            if " DBA " in f" {upper} ":  # index the brand name too: "0965688 BC LTD DBA PROCOGIA"
                names.append(employer[upper.index("DBA") + 3:])
            for name in names:
                key = normalize_company(name)
                if len(key) < 2:
                    continue
                rec = agg.setdefault(key, [0, 0, 0, {}])
                rec[0] += initial
                rec[1] += continuing
                rec[2] = max(rec[2], year)
                rec[3][employer] = rec[3].get(employer, 0) + initial + continuing
            n += 1
        print(f"  {n} employer rows", flush=True)

    employers = {k: [v[0], v[1], v[2], max(v[3], key=v[3].get)] for k, v in agg.items()}
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with gzip.open(DATA_PATH, "wt", encoding="utf-8", compresslevel=9) as f:
        json.dump({"years": sorted(y for y in years if y), "employers": employers}, f, separators=(",", ":"))
    print(f"Wrote {len(employers)} employers to {DATA_PATH} ({os.path.getsize(DATA_PATH) // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1:] or ["2021", "2022", "2023"])

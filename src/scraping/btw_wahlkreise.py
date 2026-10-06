#!/usr/bin/env python3
"""
Generated with Claude and Mistral

Bundestag elections: download and process constituency results.

Step 1: Download btw_kerg.zip ("Endgültige Ergebnisse nach Wahlkreisen aller
           Bundestagswahlen" / "Final results by constituency for all federal
           elections") from the Federal Returning Officer's website and
           extract it to ../../data/raw.
Step 2: For all elections from MIN_YEAR onwards, read the kerg file (not
           kerg2) and write a table with one row per constituency to
           ../../data/interim.

Paths are relative to the location of this script

Requires:  pip install requests
Usage:     python btw_wahlkreise.py [--force] [--min-year 2013]
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin

import requests

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = (BASE_DIR / "../../data/raw").resolve()
INTERIM_DIR = (BASE_DIR / "../../data/interim").resolve()

PAGE_URL = "https://www.bundeswahlleiterin.de/bundestagswahlen/2025/ergebnisse.html"
# Fallback in case the link on the page cannot be found
FALLBACK_ZIP_URL = (
    "https://www.bundeswahlleiterin.de/dam/jcr/"
    "ce2d2b6a-f211-4355-8eea-355c98cd4e47/btw_kerg.zip"
)
ZIP_NAME = "btw_kerg.zip"
EXTRACT_DIR = RAW_DIR / "btw_kerg"
OUTPUT_NAME = "btw_wahlkreise.csv"

MIN_YEAR = 2013  # inclusive ("from 2013"); can be changed via --min-year
EXPECTED_WAHLKREISE = 299

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; btw-kerg-pipeline/1.0)"}

# Party groups in the order of the output table ("Sonstige"/"Other" comes last)
GROUPS = ["CDU/CSU", "SPD", "Grüne", "FDP", "Linke", "AfD", "FW", "BSW"]
OTHER = "Sonstige"

FIELDNAMES = (
    ["Jahr", "Wahlkreis", "Wahlberechtigte", "Wählende", "Direkt gewählter Abgeordnete"]
    + [f"Erststimmen {g}" for g in GROUPS + [OTHER]]
    + [f"Zweitstimmen {g}" for g in GROUPS + [OTHER]]
)

DASHES = {"", "-", "–", "—", "‑"}
KIND_MAP = {"erststimmen": "erst", "zweitstimmen": "zweit"}


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
def norm(text: str) -> str:
    """Normalize whitespace, casefold, remove trailing period."""
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip().casefold().rstrip(".")


def classify(name: str) -> str | None:
    """Party name (long form or abbreviation, depending on election year) -> party group."""
    n = norm(name)
    if n in {"cdu", "csu"} or n.startswith(
        ("christlich demokratische union", "christlich-soziale union")
    ):
        return "CDU/CSU"
    if n == "spd" or n.startswith("sozialdemokratische partei"):
        return "SPD"
    if n in {"grüne", "gruene"} or n.startswith("bündnis 90"):
        return "Grüne"
    if n == "fdp" or n.startswith("freie demokratische partei"):
        return "FDP"
    if n in {"die linke", "linke"}:
        return "Linke"
    if n == "afd" or n.startswith("alternative für deutschland"):
        return "AfD"
    if n in {"freie wähler", "fw"}:
        return "FW"
    if n == "bsw" or n.startswith("bündnis sahra wagenknecht"):
        return "BSW"
    return None


def block_role(name: str) -> str:
    """Is the column group a metric or a party?"""
    n = norm(name)
    if n.startswith("wahlberechtigte"):
        return "wahlberechtigte"
    if n in {"wähler", "wählende", "waehler", "waehlende"}:
        return "waehlende"
    if n.startswith("ungültige"):
        return "ungueltige"
    if n.startswith("gültige"):
        return "gueltige"
    if n in {"übrige", "sonstige"}:
        return "uebrige"
    return "party"


def to_int(value: str) -> int:
    value = value.strip().replace(".", "").replace(" ", "")
    if value in DASHES:
        return 0
    try:
        return int(value)
    except ValueError:
        return int(float(value.replace(",", ".")))


def cell(row: list[str], idx: int | None) -> int:
    if idx is None or idx >= len(row):
        return 0
    return to_int(row[idx])


# --------------------------------------------------------------------------- #
# Step 1: Download + extract
# --------------------------------------------------------------------------- #
def find_zip_url(session: requests.Session) -> str:
    """Read the btw_kerg.zip link from the results page (the hash in the path may change)."""
    try:
        resp = session.get(PAGE_URL, timeout=30)
        resp.raise_for_status()
        match = re.search(r'href="([^"]*btw_kerg\.zip)"', resp.text)
        if match:
            return urljoin(PAGE_URL, match.group(1))
        print("Note: link not found on the page, using fallback URL.")
    except requests.RequestException as exc:
        print(f"Note: page could not be read ({exc}), using fallback URL.")
    return FALLBACK_ZIP_URL


def download_zip(force: bool) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = RAW_DIR / ZIP_NAME
    if zip_path.exists() and not force:
        print(f"ZIP already exists, skipping download: {zip_path}")
        return zip_path

    with requests.Session() as session:
        session.headers.update(HTTP_HEADERS)
        url = find_zip_url(session)
        print(f"Downloading {url}")
        tmp_path = zip_path.with_suffix(".zip.part")
        with session.get(url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            with open(tmp_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    fh.write(chunk)

    if not zipfile.is_zipfile(tmp_path):
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError("The downloaded file is not a valid ZIP file.")
    tmp_path.replace(zip_path)
    print(f"Saved: {zip_path}")
    return zip_path


def extract_zip(zip_path: Path) -> Path:
    EXTRACT_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(EXTRACT_DIR)
    print(f"Extracted to: {EXTRACT_DIR}")
    return EXTRACT_DIR


# --------------------------------------------------------------------------- #
# Step 2: Read kerg files
# --------------------------------------------------------------------------- #
def find_kerg_files(root: Path) -> list[Path]:
    """All *kerg*.csv, but not *kerg2*.csv."""
    files = []
    for path in sorted(root.rglob("*.csv")):
        name = path.name.casefold()
        if "__macosx" in str(path).casefold() or name.startswith("._"):
            continue
        if "kerg" in name and "kerg2" not in name:
            files.append(path)
    return files


def read_rows(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return list(csv.reader(io.StringIO(text), delimiter=";"))


def detect_year(rows: list[list[str]], path: Path) -> int | None:
    for row in rows[:10]:
        m = re.search(r"Bundestagswahl\s+(\d{4})", " ".join(row))
        if m:
            return int(m.group(1))
    m = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", path.name)
    if m:
        return int(m.group(1))
    m = re.search(r"btw(\d{2})", path.name, re.IGNORECASE)
    if m:
        yy = int(m.group(1))
        return 2000 + yy if yy < 50 else 1900 + yy
    return None


def find_header_row(rows: list[list[str]]) -> int:
    for i, row in enumerate(rows):
        if row and norm(row[0]) == "nr":
            return i
    raise ValueError("Header row (starting with 'Nr') not found")


def parse_kerg_file(path: Path) -> tuple[int | None, list[dict], int]:
    """
    Read a kerg file (wide format).

    Structure: a header row with group names (4 columns each), followed by
    an "Erststimmen/Zweitstimmen" (first/second votes) row and an
    "Endgültig/Vorperiode" (final/previous period) row. Each group therefore
    has 4 columns: first final, first previous, second final, second
    previous. Only the final values are used.

    Returns: (year, rows, number of winners determined by first votes)
    """
    rows = read_rows(path)
    year = detect_year(rows, path)
    h = find_header_row(rows)

    names, kinds = rows[h], rows[h + 1]
    period_row_ok = h + 2 < len(rows) and any(
        norm(c).startswith("endg") for c in rows[h + 2]
    )
    periods = rows[h + 2] if period_row_ok else []
    data_start = h + 3 if period_row_ok else h + 2

    # --- Map columns ------------------------------------------------------- #
    blocks: dict[str, dict[str, int]] = {}
    winner_col: int | None = None
    block: str | None = None
    kind: str | None = None
    width = max(len(names), len(kinds), len(periods))
    for j in range(width):
        name = names[j].strip() if j < len(names) else ""
        k = norm(kinds[j]) if j < len(kinds) else ""
        if k:
            kind = k
        if name:
            n = norm(name)
            if n == "gewählt" or n.startswith("direkt"):
                winner_col = j
                block = None
                continue
            block = name
        if block is None or kind not in KIND_MAP:
            continue
        if period_row_ok:
            p = norm(periods[j]) if j < len(periods) else ""
            if not p.startswith("endg"):
                continue
        blocks.setdefault(block, {}).setdefault(KIND_MAP[kind], j)

    meta: dict[str, dict[str, int]] = {}
    parties: dict[str, dict[str, int]] = {}
    for bname, idx in blocks.items():
        role = block_role(bname)
        if role == "party":
            parties[bname] = idx
        else:
            meta[role] = idx
    for needed in ("wahlberechtigte", "waehlende"):
        if needed not in meta:
            raise ValueError(f"Column '{needed}' not found")

    party_names = {norm(p): p for p in parties}
    unknown_winner_values: set[str] = set()

    # --- Data rows --------------------------------------------------------- #
    out: list[dict] = []
    fallback_winners = 0
    for row in rows[data_start:]:
        if len(row) < 4 or not row[0].strip().isdigit():
            continue
        nr, gebiet, parent = row[0].strip(), row[1].strip(), row[2].strip()
        # Skip states ("gehört zu" = 99) and the federal territory (empty "gehört zu")
        if parent in ("", "99"):
            continue

        erst: dict[str, int] = defaultdict(int)
        zweit: dict[str, int] = defaultdict(int)
        party_erst: dict[str, int] = {}
        for pname, idx in parties.items():
            e = cell(row, idx.get("erst"))
            z = cell(row, idx.get("zweit"))
            party_erst[pname] = e
            g = classify(pname)
            if g:
                erst[g] += e
                zweit[g] += z

        def remainder(stimme: str, sums: dict[str, int]) -> int:
            named = sum(sums[g] for g in GROUPS)
            valid = cell(row, meta.get("gueltige", {}).get(stimme))
            if valid:
                return valid - named
            rest = sum(
                cell(row, idx.get(stimme))
                for pname, idx in parties.items()
                if not classify(pname)
            )
            return rest + cell(row, meta.get("uebrige", {}).get(stimme))

        erst[OTHER] = remainder("erst", erst)
        zweit[OTHER] = remainder("zweit", zweit)
        if erst[OTHER] < 0 or zweit[OTHER] < 0:
            print(f"  Warning {year} constituency {nr}: negative 'other' votes")

        # --- Directly elected ---------------------------------------------- #
        winner: str | None = None
        if winner_col is not None and winner_col < len(row):
            wv = row[winner_col].strip()
            if wv not in DASHES:
                winner = classify(wv)
                if winner is None and norm(wv) in party_names:
                    winner = OTHER  # e.g. SSW or another minor party
                elif winner is None:
                    unknown_winner_values.add(wv)
        if winner is None:
            # "-" or missing column: winner = highest first-vote count
            if party_erst and max(party_erst.values()) > 0:
                top = max(party_erst, key=party_erst.get)
                winner = classify(top) or OTHER
                fallback_winners += 1

        wahlberechtigte = cell(row, meta["wahlberechtigte"].get("erst")) or cell(
            row, meta["wahlberechtigte"].get("zweit")
        )
        waehlende = cell(row, meta["waehlende"].get("erst")) or cell(
            row, meta["waehlende"].get("zweit")
        )

        rec = {
            "Jahr": year,
            "Wahlkreis": gebiet,
            "Wahlberechtigte": wahlberechtigte,
            "Wählende": waehlende,
            "Direkt gewählter Abgeordnete": winner or "",
            "_nr": int(nr),
        }
        for g in GROUPS + [OTHER]:
            rec[f"Erststimmen {g}"] = erst[g]
            rec[f"Zweitstimmen {g}"] = zweit[g]
        out.append(rec)

    if unknown_winner_values:
        print(
            f"  Note {year}: unknown values in the 'Gewählt' (elected) column "
            f"(e.g. {sorted(unknown_winner_values)[:3]}), winner determined by first votes."
        )
    return year, out, fallback_winners


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--force", action="store_true", help="download the ZIP even if it already exists")
    ap.add_argument("--min-year", type=int, default=MIN_YEAR, help="first election year to include")
    args = ap.parse_args()

    # 1) Download + extract
    zip_path = download_zip(args.force)
    root = extract_zip(zip_path)

    # 2) Process kerg files
    files = find_kerg_files(root)
    if not files:
        print("No kerg files found in the ZIP.", file=sys.stderr)
        return 1
    print(f"Found kerg files: {[f.name for f in files]}")

    by_year: dict[int, Path] = {}
    for f in files:
        try:
            year = detect_year(read_rows(f), f)
        except Exception as exc:  # noqa: BLE001
            print(f"Skipping {f.name}: {exc}")
            continue
        if year is None:
            print(f"Skipping {f.name}: election year not recognized")
        elif year < args.min_year:
            continue
        elif year in by_year:
            print(f"Warning: multiple files for {year}, using {by_year[year].name}")
        else:
            by_year[year] = f

    all_rows: list[dict] = []
    for year in sorted(by_year):
        path = by_year[year]
        print(f"Processing {year}: {path.name}")
        _, rows, fallback = parse_kerg_file(path)
        print(f"  {len(rows)} constituencies, {fallback}x winner determined by first votes")
        if len(rows) != EXPECTED_WAHLKREISE:
            print(f"  Warning: expected {EXPECTED_WAHLKREISE} constituencies, found {len(rows)}")
        all_rows.extend(rows)

    if not all_rows:
        print("No data extracted.", file=sys.stderr)
        return 1

    all_rows.sort(key=lambda r: (r["Jahr"], r["_nr"]))

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    out_path = INTERIM_DIR / OUTPUT_NAME
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"Done: {len(all_rows)} rows -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
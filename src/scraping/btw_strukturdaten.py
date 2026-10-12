#!/usr/bin/env python3
"""
Generated with Claude

Bundestag elections: download and harmonise the official constituency
structural data ("Strukturdaten für die Wahlkreise") of the Bundeswahlleiterin
for the federal elections 2013, 2017, 2021 and 2025.

Reads:   one CSV per election from bundeswahlleiterin.de, cached in
         data/raw/btw_strukturdaten/ (download is skipped if the file exists).
Writes:  data/interim/btw_strukturdaten.csv, one row per constituency and
         election (299 rows per year), with a common set of columns.
Run:     python3 -m src.scraping.btw_strukturdaten        (from the project root)

What is done:
- The source files are semicolon-separated with a few "#" preamble lines and a
  "Spalten-Nr." row. 2013 and 2017 are encoded in CP 1252, 2021 and 2025 in
  UTF-8 with BOM (stated in the file preamble); both are tried explicitly.
- Only constituency rows (Wahlkreis-Nr. 1..299) are kept. The per-state
  summary rows ("Land insgesamt", Nr. 901..916) and the federal row
  ("Insgesamt", Nr. 999) are dropped, because the same aggregates are already
  in btw_results.csv.
- The set and naming of variables differs between years, so a core set is
  mapped to common column names via COLUMN_MAP (per-year source column name).
  Numbers are converted from German format (decimal comma, thousands point in
  the 2021 file) to plain floats. The source uses "–" (2013) and "." (2017) as
  missing markers; these become NaN.
- Variables that a year does not publish are left NaN:
    * 2013 has no "Verfügbares Einkommen" and no "Bruttoinlandsprodukt".
    * 2013 has no foreigner share; it is DERIVED here as
      (Bevölkerung insgesamt - Deutsche) / Bevölkerung insgesamt * 100 from the
      two published population counts (in 1000), the only computed column.
    * Vehicle density: 2013 and 2017 publish all motor vehicles
      (Kfz_je_1000_EW), 2021 and 2025 publish passenger cars only
      (Pkw_je_1000_EW). They are kept as two columns, not merged.
- Definitions that differ slightly between years are kept in one column but
  noted here: the "Abitur" share is "allgemeine Hochschulreife" in 2013 and
  "allgemeine und Fachhochschulreife" from 2017 on; the 2013 age bands are
  labelled "18-25, 25-35, ..." but mean "from ... to under ...", i.e. the same
  bands as "18-24, 25-34, ..." later; the reference dates of the unemployment
  rate and SGB II counts differ by year (see the source column names).
- Footnotes from the source are kept in the column `Fussnote`. They matter:
  for cities with several constituencies (Berlin, Hamburg, München, Köln, ...)
  most variables are city-wide values repeated for each constituency.
- As a check, the constituency names are compared with
  data/interim/btw_wahlkreise.csv per year (match rate is printed; that file
  is not changed).

Requires:  pip install requests pandas
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = (BASE_DIR / "../../data/raw/btw_strukturdaten").resolve()
INTERIM_DIR = (BASE_DIR / "../../data/interim").resolve()
OUTPUT_PATH = INTERIM_DIR / "btw_strukturdaten.csv"
WAHLKREISE_PATH = INTERIM_DIR / "btw_wahlkreise.csv"

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; dalas-strukturdaten/1.0)"}
EXPECTED_WAHLKREISE = 299

# Per election: the page that links the CSV, and the direct link found there on
# 2026-10-12 as fallback (the /dam/jcr/ hash in the path may change).
SOURCES = {
    2013: {
        "page": "https://www.bundeswahlleiterin.de/bundestagswahlen/2013/strukturdaten.html",
        "csv": "https://www.bundeswahlleiterin.de/dam/jcr/65ef1c2d-4df0-44e2-8881-99a176b4896c/btw2013_strukturdaten.csv",
    },
    2017: {
        "page": "https://www.bundeswahlleiterin.de/bundestagswahlen/2017/strukturdaten.html",
        "csv": "https://www.bundeswahlleiterin.de/dam/jcr/f7566722-a528-4b18-bea3-ea419371e300/btw2017_strukturdaten.csv",
    },
    2021: {
        "page": "https://www.bundeswahlleiterin.de/bundestagswahlen/2021/strukturdaten.html",
        "csv": "https://www.bundeswahlleiterin.de/dam/jcr/b1d3fc4f-17eb-455f-a01c-a0bf32135c5d/btw21_strukturdaten.csv",
    },
    2025: {
        "page": "https://www.bundeswahlleiterin.de/bundestagswahlen/2025/strukturdaten.html",
        "csv": "https://www.bundeswahlleiterin.de/dam/jcr/181f9e38-38db-4f64-991c-8141dfa0f2cb/btw2025_strukturdaten.csv",
    },
}

# Target column -> {year: exact source column name}. A missing year means the
# variable is not published for that election and stays NaN.
# "EW" = Einwohner. Shares are in percent, as in the source.
COLUMN_MAP = {
    "Flaeche_km2": {
        2013: "Fläche am 31.12.2011 (km²)",
        2017: "Fläche am 31.12.2015 (km²)",
        2021: "Fläche am 31.12.2019 (km²)",
        2025: "Fläche am 31.12.2023 (km²)",
    },
    "Bevoelkerung_1000": {
        2013: "Bevölkerung am 30.09.2012 - Insgesamt (in 1000)",
        2017: "Bevölkerung am 31.12.2015 - Insgesamt (in 1000)",
        2021: "Bevölkerung am 31.12.2019 - Insgesamt (in 1000)",
        2025: "Bevölkerung am 31.12.2023 - Insgesamt (in 1000)",
    },
    # Only used to derive the 2013 foreigner share (see derive_2013_auslaender).
    "Deutsche_1000": {
        2013: "Bevölkerung am 30.09.2012 - Deutsche (in 1000)",
    },
    "Bevoelkerungsdichte_EW_je_km2": {
        2013: "Bevölkerungsdichte am 31.12.2011 (Einwohner je km²",  # sic, bracket missing in source
        2017: "Bevölkerungsdichte am 31.12.2015 (Einwohner je km²)",
        2021: "Bevölkerungsdichte am 31.12.2019 (EW je km²)",
        2025: "Bevölkerungsdichte am 31.12.2023 (EW je km²)",
    },
    "Auslaender_Pct": {
        # 2013: not published, derived from population counts
        2017: "Bevölkerung am 31.12.2015 - Ausländer (%)",
        2021: "Bevölkerung am 31.12.2019 - Ausländer/-innen (%)",
        2025: "Bevölkerung am 31.12.2023 - Ausländer/-innen (%)",
    },
    "Wanderungssaldo_je_1000_EW": {
        2013: "Zu- (+) bzw. Abnahme (-) der Bevölkerung 2011 - Wanderungssaldo (je 1000 Einwohner)",
        2017: "Zu- (+) bzw. Abnahme (-) der Bevölkerung 2015 - Wanderungssaldo (je 1000 Einwohner)",
        2021: "Zu- (+) bzw. Abnahme (-) der Bevölkerung 2019 - Wanderungssaldo (je 1000 EW)",
        2025: "Zu- (+) bzw. Abnahme (-) der Bevölkerung 2022 - Wanderungssaldo (je 1000 EW)",
    },
    "Alter_unter_18_Pct": {
        2013: "Alter von ... bis unter ... Jahren am 31.12.2011 - unter 18 (%)",
        2017: "Alter von ... bis ... Jahren am 31.12.2015 - unter 18 (%)",
        2021: "Alter von ... bis ... Jahren am 31.12.2019 - unter 18 (%)",
        2025: "Alter von ... bis ... Jahren am 31.12.2023 - unter 18 (%)",
    },
    "Alter_18_24_Pct": {
        2013: "Alter von ... bis unter ... Jahren am 31.12.2011 - 18-25 (%)",
        2017: "Alter von ... bis ... Jahren am 31.12.2015 - 18-24 (%)",
        2021: "Alter von ... bis ... Jahren am 31.12.2019 - 18-24 (%)",
        2025: "Alter von ... bis ... Jahren am 31.12.2023 - 18-24 (%)",
    },
    "Alter_25_34_Pct": {
        2013: "Alter von ... bis unter ... Jahren am 31.12.2011 - 25-35 (%)",
        2017: "Alter von ... bis ... Jahren am 31.12.2015 - 25-34 (%)",
        2021: "Alter von ... bis ... Jahren am 31.12.2019 - 25-34 (%)",
        2025: "Alter von ... bis ... Jahren am 31.12.2023 - 25-34 (%)",
    },
    "Alter_35_59_Pct": {
        2013: "Alter von ... bis unter ... Jahren am 31.12.2011 - 35-60 (%)",
        2017: "Alter von ... bis ... Jahren am 31.12.2015 - 35-59 (%)",
        2021: "Alter von ... bis ... Jahren am 31.12.2019 - 35-59 (%)",
        2025: "Alter von ... bis ... Jahren am 31.12.2023 - 35-59 (%)",
    },
    "Alter_60_74_Pct": {
        2013: "Alter von ... bis unter ... Jahren am 31.12.2011 - 60-75 (%)",
        2017: "Alter von ... bis ... Jahren am 31.12.2015 - 60-74 (%)",
        2021: "Alter von ... bis ... Jahren am 31.12.2019 - 60-74 (%)",
        2025: "Alter von ... bis ... Jahren am 31.12.2023 - 60-74 (%)",
    },
    "Alter_75plus_Pct": {
        2013: "Alter von ... bis unter ... Jahren am 31.12.2011 - 75 und mehr (%)",
        2017: "Alter von ... bis ... Jahren am 31.12.2015 - 75 und mehr (%)",
        2021: "Alter von ... bis ... Jahren am 31.12.2019 - 75 und mehr (%)",
        2025: "Alter von ... bis ... Jahren am 31.12.2023 - 75 und mehr (%)",
    },
    "Schulabgaenger_ohne_Hauptschulabschluss_Pct": {
        2013: "Allgemein bildendes Schulwesen: Absolventen/Abgänger nach dem Abschluss 2011 - ohne Hauptschulabschluss (%)",
        2017: "Absolventen/Abgänger allgemeinbildender Schulen 2015 - ohne Hauptschulabschluss (%)",
        2021: "Schulabgänger/-innen allgemeinbildender Schulen 2019 - ohne Hauptschulabschluss (%)",
        2025: "Schulabgänger/-innen allgemeinbildender Schulen 2022 - ohne Hauptschulabschluss (%)",
    },
    "Schulabgaenger_Abitur_Pct": {
        # 2013: general Abitur only; 2017+: general plus Fachhochschulreife
        2013: "Allgemein bildendes Schulwesen: Absolventen/Abgänger nach dem Abschluss 2011 - mit allgemeiner Hochschulreife (%)",
        2017: "Absolventen/Abgänger allgemeinbildender Schulen 2015 - mit allgemeiner und Fachhochschulreife (%)",
        2021: "Schulabgänger/-innen allgemeinblldender Schulen 2019 - mit allgemeiner und Fachhochschulreife (%)",  # sic "allgemeinblldender"
        2025: "Schulabgänger/-innen allgemeinblldender Schulen 2022 - mit allgemeiner und Fachhochschulreife (%)",  # sic
    },
    "Verfuegbares_Einkommen_EUR_je_EW": {
        # 2013: not published
        2017: "Verfügbares Einkommen der privaten Haushalte 2014 (€ je Einwohner)",
        2021: "Verfügbares Einkommen der privaten Haushalte 2018 (EUR je EW)",
        2025: "Verfügbares Einkommen der privaten Haushalte 2021 (EUR je EW)",
    },
    "BIP_EUR_je_EW": {
        # 2013: not published
        2017: "Bruttoinlandsprodukt 2014 (€ je Einwohner)",
        2021: "Bruttoinlandsprodukt 2018 (EUR je EW)",
        2025: "Bruttoinlandsprodukt 2021 (EUR je EW)",
    },
    "Kfz_je_1000_EW": {
        # all motor vehicles; not published from 2021 on
        2013: "Kraftfahrzeugbestand am 01.01.2012 (je 1000 Einwohner)",
        2017: "Kraftfahrzeugbestand am 01.01.2016 (je 1000 Einwohner)",
    },
    "Pkw_je_1000_EW": {
        # passenger cars only; not published before 2021
        2021: "PKW-Bestand am 01.01.2020 - PKW insgesamt (je 1000 EW)",
        2025: "PKW-Bestand am 01.01.2024 - PKW insgesamt (je 1000 EW)",
    },
    "SV_Beschaeftigte_je_1000_EW": {
        2013: "Sozialversicherungspflichtig Beschäftigte am 30.06.2011 - insgesamt (je 1000 Einwohner)",
        2017: "Sozialversicherungspflichtig Beschäftigte am 30.06.2016 - insgesamt (je 1000 Einwohner)",
        2021: "Sozialversicherungspflichtig Beschäftigte am 30.06.2020 - insgesamt (je 1000 EW)",
        2025: "Sozialversicherungspflichtig Beschäftigte am 30.06.2023 - insgesamt (je 1000 EW)",
    },
    "Beschaeftigte_Produzierendes_Gewerbe_Pct": {
        2013: "Sozialversicherungspflichtig Beschäftigte am 30.06.2011 - Produzierendes Gewerbe (%)",
        2017: "Sozialversicherungspflichtig Beschäftigte am 30.06.2016 - Produzierendes Gewerbe (%)",
        2021: "Sozialversicherungspflichtig Beschäftigte am 30.06.2020 - Produzierendes Gewerbe (%)",
        2025: "Sozialversicherungspflichtig Beschäftigte am 30.06.2023 - Produzierendes Gewerbe (%)",
    },
    "Arbeitslosenquote_Pct": {
        2013: "Arbeitslosenquote Ende Dezember 2012 - insgesamt",
        2017: "Arbeitslosenquote März 2017 - insgesamt",
        2021: "Arbeitslosenquote Februar 2021 - insgesamt",
        2025: "Arbeitslosenquote November 2024 - insgesamt",
    },
    "SGBII_Empfaenger_je_1000_EW": {
        # the source gives recipients per 1000 inhabitants, not a percent
        2013: "Empfänger(innen) von Leistungen nach SGB II am 31.12.2012 -  insgesamt (je 1000 Einwohner)",
        2017: "Empfänger(innen) von Leistungen nach SGB II am 31.12.2016 -  insgesamt (je 1000 Einwohner)",
        2021: "Empfänger/-innen von Leistungen nach SGB II  Oktober 2020 -  insgesamt (je 1000 EW)",
        2025: "Empfänger/-innen von Leistungen nach SGB II  August 2024 -  insgesamt (je 1000 EW)",
    },
}

ID_COLUMNS = ["Jahr", "Wahlkreis_Nr", "Wahlkreis", "Land"]
# Output order; Deutsche_1000 is only a helper for the 2013 derivation and is not written.
OUTPUT_COLUMNS = ID_COLUMNS + [c for c in COLUMN_MAP if c != "Deutsche_1000"] + ["Fussnote"]

MISSING_MARKERS = {"", "-", "–", ".", "x", "X"}


# --------------------------------------------------------------------------- #
# Step 1: Download (cached)
# --------------------------------------------------------------------------- #
def find_csv_url(session: requests.Session, year: int) -> str:
    """Read the strukturdaten CSV link from the election page, fallback to the known URL."""
    page = SOURCES[year]["page"]
    try:
        resp = session.get(page, timeout=30)
        resp.raise_for_status()
        match = re.search(r'href="([^"]*strukturdaten\.csv)"', resp.text)
        if match:
            return urljoin(page, match.group(1))
        print(f"  Note {year}: CSV link not found on the page, using fallback URL.")
    except requests.RequestException as exc:
        print(f"  Note {year}: page could not be read ({exc}), using fallback URL.")
    return SOURCES[year]["csv"]


def download(year: int) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"btw{year}_strukturdaten.csv"
    if path.exists():
        print(f"{year}: raw file exists, skipping download: {path.name}")
        return path
    with requests.Session() as session:
        session.headers.update(HTTP_HEADERS)
        url = find_csv_url(session, year)
        print(f"{year}: downloading {url}")
        resp = session.get(url, timeout=120)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    return path


# --------------------------------------------------------------------------- #
# Step 2: Read and harmonise one file
# --------------------------------------------------------------------------- #
def read_text(path: Path) -> str:
    """Decode the raw file. 2021/2025 are UTF-8 with BOM, 2013/2017 CP 1252
    (both stated in the file preamble). utf-8-sig is tried first because a
    CP 1252 file with umlauts fails UTF-8 decoding, while the reverse is not
    detected."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Cannot decode {path}")


def to_float(value) -> float:
    """German number format -> float. '2.124,3' -> 2124.3; missing markers -> NaN."""
    if value is None:
        return float("nan")
    value = str(value).strip()
    if value in MISSING_MARKERS:
        return float("nan")
    # Points are thousands separators (only the 2021 file uses them), comma is the decimal.
    return float(value.replace(".", "").replace(",", "."))


def parse_file(path: Path, year: int) -> pd.DataFrame:
    text = read_text(path)
    lines = text.splitlines()
    # The header is the first line starting with "Land;"; everything before it
    # is preamble ("#" comments and, from 2017 on, the "Spalten-Nr." row).
    header_idx = next(i for i, line in enumerate(lines) if line.startswith("Land;"))
    raw = pd.read_csv(
        io.StringIO(text), sep=";", skiprows=header_idx, dtype=str, keep_default_na=False
    )
    raw.columns = [c.strip() for c in raw.columns]

    # Keep constituencies only: Nr 1..299. This drops the states (901..916),
    # the federal row (999) and the 2013 "Spalten-Nr." row (empty Nr).
    nr = pd.to_numeric(raw["Wahlkreis-Nr."].str.strip(), errors="coerce")
    df = raw[(nr >= 1) & (nr <= EXPECTED_WAHLKREISE)].copy()
    df["Wahlkreis_Nr"] = nr[df.index].astype(int)

    out = pd.DataFrame(
        {
            "Jahr": year,
            "Wahlkreis_Nr": df["Wahlkreis_Nr"].values,
            "Wahlkreis": df["Wahlkreis-Name"].str.strip().values,
            "Land": df["Land"].str.strip().values,
        }
    )
    for target, per_year in COLUMN_MAP.items():
        source = per_year.get(year)
        if source is None:
            out[target] = float("nan")  # variable not published this year
            continue
        if source not in df.columns:
            raise KeyError(f"{year}: column not found: {source!r}")
        out[target] = df[source].map(to_float).values
    out["Fussnote"] = df["Fußnoten"].str.strip().values if "Fußnoten" in df.columns else ""
    return out


def derive_2013_auslaender(out: pd.DataFrame) -> pd.DataFrame:
    """2013 publishes no foreigner share, only total and German population (in 1000).
    The share is computed from those two counts; later years use the published share."""
    is_2013 = out["Jahr"] == 2013
    out.loc[is_2013, "Auslaender_Pct"] = (
        (out.loc[is_2013, "Bevoelkerung_1000"] - out.loc[is_2013, "Deutsche_1000"])
        / out.loc[is_2013, "Bevoelkerung_1000"]
        * 100
    ).round(1)
    return out


# --------------------------------------------------------------------------- #
# Step 3: Checks
# --------------------------------------------------------------------------- #
def normalise_name(name: str) -> str:
    """For the join check only: unify dash variants and the spacing around them
    (kerg 2013 writes "Berlin-Treptow - Köpenick", Strukturdaten 2013
    "Berlin-Treptow-Köpenick"). The output keeps the source names."""
    name = name.replace("–", "-").replace("—", "-")
    name = re.sub(r"\s*-\s*", "-", name)
    return re.sub(r"\s+", " ", name).strip().casefold()


def check_join(out: pd.DataFrame) -> None:
    """Compare constituency names per year with btw_wahlkreise.csv (not modified)."""
    if not WAHLKREISE_PATH.exists():
        print(f"Join check skipped, {WAHLKREISE_PATH.name} not found.")
        return
    wk = pd.read_csv(WAHLKREISE_PATH, usecols=["Jahr", "Wahlkreis"])
    print("Join check against btw_wahlkreise.csv (names, dash-normalised):")
    for year, group in wk.groupby("Jahr"):
        names_wk = set(group["Wahlkreis"].map(normalise_name))
        names_sd = set(out.loc[out["Jahr"] == year, "Wahlkreis"].map(normalise_name))
        exact = len(set(group["Wahlkreis"]) & set(out.loc[out["Jahr"] == year, "Wahlkreis"]))
        matched = names_wk & names_sd
        print(
            f"  {year}: {len(matched)}/{len(names_wk)} names match "
            f"({exact} exact without normalisation)"
        )
        for name in sorted(names_wk - names_sd):
            print(f"    only in btw_wahlkreise: {name}")
        for name in sorted(names_sd - names_wk):
            print(f"    only in strukturdaten:  {name}")


def plausibility(out: pd.DataFrame) -> None:
    for year, group in out.groupby("Jahr"):
        n = len(group)
        flag = "" if n == EXPECTED_WAHLKREISE else f"  <-- expected {EXPECTED_WAHLKREISE}"
        age_sum = group[[c for c in group.columns if c.startswith("Alter_")]].sum(axis=1)
        densest = group.loc[group["Bevoelkerungsdichte_EW_je_km2"].idxmax()]
        print(
            f"  {year}: {n} rows{flag}; age shares sum {age_sum.min():.1f}..{age_sum.max():.1f}; "
            f"densest: {densest['Wahlkreis']} ({densest['Bevoelkerungsdichte_EW_je_km2']:.0f} EW/km²); "
            f"unemployment {group['Arbeitslosenquote_Pct'].min():.1f}..{group['Arbeitslosenquote_Pct'].max():.1f} %"
        )
        missing = group.drop(columns=["Fussnote"]).isna().sum()
        missing = missing[missing > 0]
        if len(missing):
            print(f"    NaN counts: {missing.to_dict()}")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    frames = []
    for year in sorted(SOURCES):
        path = download(year)
        frames.append(parse_file(path, year))
    out = pd.concat(frames, ignore_index=True)
    out = derive_2013_auslaender(out)
    out = out[OUTPUT_COLUMNS].sort_values(["Jahr", "Wahlkreis_Nr"]).reset_index(drop=True)

    print("Row counts and plausibility:")
    plausibility(out)
    check_join(out)

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUTPUT_PATH, index=False, encoding="utf-8")
    print(f"Done: {len(out)} rows -> {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Preprocess the structural data of the Bundestag constituencies (Strukturdaten für die Wahlkreise).

Reads : data/interim/btw_strukturdaten.csv   (written by src/scraping/btw_strukturdaten.py)
Writes: data/processed/btw_strukturdaten.csv
Run   : python src/preprocessing/btw_strukturdaten.py   (works from any working directory)

One row per election and constituency (4 elections x 299 Wahlkreise = 1196 rows). The key columns are the
same as in data/processed/btw_results.csv (Date, State, Constituency number, Constituency), so both tables
join on Date + Constituency number. Constituency numbers are only valid within one election: boundaries were
redrawn (18 new constituencies 2013-2025) and numbers shifted when seats moved between states (184
renumbered 2021 -> 2025). Compare constituencies within one election, not across elections.

Processing decisions (reasons in docs/decisions.md, 2026-11-20). Values are never changed or rounded:
    - Kfz_je_1000_EW (2013, 2017: all motor vehicles) and Pkw_je_1000_EW (2021, 2025: cars only) are
      dropped. The two measures are defined differently, so one column would show a drop that is only a
      change of definition.
    - Disposable income and GDP stay empty for 2013, because the 2013 source does not publish them.
    - "School leavers with Abitur pct" counts the general Abitur only in 2013 and Abitur plus
      Fachhochschulreife from 2017 on. The values are kept; the flag column
      "Abitur incl. Fachhochschulreife" marks the definition of every row.
    - "Area-wide values" flags constituencies where, for some variables, the source reports the value of a
      larger area that is repeated in several constituencies (see the footnote text), in two cases:
        * a city forms several constituencies ("Die Stadt ... bildet mehrere Wahlkreise"): Berlin, Dortmund,
          Duisburg, Düsseldorf, Essen, Frankfurt am Main, Hamburg, Hannover, Köln, Leipzig, München, Stuttgart;
        * a district spans several constituencies ("... daher sind in den Wahlkreisen ... die gleichen Werte
          ausgewiesen"): Region Hannover, Städteregion Aachen, Rhein-Sieg-Kreis, Kreis Mettmann,
          Kreis Recklinghausen.
      About 50 constituencies per election (40-41 city, 10-11 district). Which variables are repeated differs
      by footnote, so the flag means "some values of this row are area-wide", not all of them. Other footnotes
      (e.g. "Spalte 1: Ohne den Kölner Stadtbezirk Mülheim") only concern the number of municipalities and
      are not flagged. The raw footnote is kept in "Footnote".
    - "Region" (East / West) comes from get_bundesland_classification in labels.py, the same
      classification as the results map; Berlin counts as East.

Known empty values (left empty, as in the source):
    - Disposable income and GDP: all 299 constituencies of 2013 (not published).
    - Employees in manufacturing: 6 constituencies of 2017 (26, 51, 54, 55, 193, 196); the source has the
      missing marker "." there.
"""

from pathlib import Path

import pandas as pd

from btw_results import to_election_date
from labels import get_bundesland_classification


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

# Project root is two levels above this file (src/preprocessing/btw_strukturdaten.py)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = PROJECT_ROOT / "data" / "interim" / "btw_strukturdaten.csv"
OUTPUT_FILE = PROJECT_ROOT / "data" / "processed" / "btw_strukturdaten.csv"

# Number of constituencies in every Bundestag election since 2002
CONSTITUENCIES_PER_ELECTION = 299

# Vehicle density: two differently defined measures, see the docstring
DROPPED_COLUMNS = ["Kfz_je_1000_EW", "Pkw_je_1000_EW"]

# From this election on, the Abitur share includes the Fachhochschulreife
ABITUR_INCL_FHR_FROM = 2017

# Footnote texts that mark values repeated across constituencies: a city forming several constituencies,
# and a district (Kreis, Region) whose values are shown in each of its constituencies
AREA_WIDE_FOOTNOTES = ["bildet mehrere Wahlkreise", "die gleichen Werte ausgewiesen"]

# Same region names as in src/analysis/result_map.py
REGION_NAMES = {"W": "West", "E": "East"}

# Interim (German) column name -> processed (English) column name, in output order
RENAME = {
    "Land": "State",
    "Wahlkreis_Nr": "Constituency number",
    "Wahlkreis": "Constituency",
    "Flaeche_km2": "Area km2",
    "Bevoelkerung_1000": "Population (1000)",
    "Bevoelkerungsdichte_EW_je_km2": "Population density per km2",
    "Auslaender_Pct": "Foreigners pct",
    "Wanderungssaldo_je_1000_EW": "Net migration per 1000 inhabitants",
    "Alter_unter_18_Pct": "Age under 18 pct",
    "Alter_18_24_Pct": "Age 18-24 pct",
    "Alter_25_34_Pct": "Age 25-34 pct",
    "Alter_35_59_Pct": "Age 35-59 pct",
    "Alter_60_74_Pct": "Age 60-74 pct",
    "Alter_75plus_Pct": "Age 75+ pct",
    "Schulabgaenger_ohne_Hauptschulabschluss_Pct": "School leavers without qualification pct",
    "Schulabgaenger_Abitur_Pct": "School leavers with Abitur pct",
    "Verfuegbares_Einkommen_EUR_je_EW": "Disposable income EUR per inhabitant",
    "BIP_EUR_je_EW": "GDP EUR per inhabitant",
    "SV_Beschaeftigte_je_1000_EW": "Employees per 1000 inhabitants",
    "Beschaeftigte_Produzierendes_Gewerbe_Pct": "Employees in manufacturing pct",
    "Arbeitslosenquote_Pct": "Unemployment rate pct",
    "SGBII_Empfaenger_je_1000_EW": "SGB II recipients per 1000 inhabitants",
    "Fussnote": "Footnote",
}

# Final column order: keys first, the Abitur flag right after the Abitur share, area flag before the footnote
OUTPUT_COLUMNS = (
    ["Date", "State", "Region", "Constituency number", "Constituency"]
    + [
        name for name in RENAME.values()
        if name not in ("State", "Constituency number", "Constituency", "Footnote")
    ]
)
OUTPUT_COLUMNS.insert(OUTPUT_COLUMNS.index("School leavers with Abitur pct") + 1, "Abitur incl. Fachhochschulreife")
OUTPUT_COLUMNS += ["Area-wide values", "Footnote"]


# --------------------------------------------------------------------------------------
# Helper functions
# --------------------------------------------------------------------------------------

def check_input(raw: pd.DataFrame) -> None:
    """Stop if the interim file does not have exactly one row per election and constituency."""
    rows_per_year = raw["Jahr"].value_counts()
    wrong_years = rows_per_year[rows_per_year != CONSTITUENCIES_PER_ELECTION]
    if not wrong_years.empty:
        raise ValueError(f"Expected {CONSTITUENCIES_PER_ELECTION} rows per election, got: {wrong_years.to_dict()}")
    if raw.duplicated(["Jahr", "Wahlkreis_Nr"]).any():
        raise ValueError("Duplicate (Jahr, Wahlkreis_Nr) rows in the interim file")


def to_region(state: str) -> str:
    """Translate a state name into 'East' or 'West'; abort on unknown states."""
    code = get_bundesland_classification(state)
    if code is None:
        raise ValueError(f"No East/West classification for state '{state}' in labels.py")
    return REGION_NAMES[code]


def build_table(raw: pd.DataFrame) -> pd.DataFrame:
    """Apply the processing decisions from the docstring and return the processed table."""
    table = raw.drop(columns=DROPPED_COLUMNS)

    # Flags are computed from the German columns, before renaming
    table["Abitur incl. Fachhochschulreife"] = table["Jahr"] >= ABITUR_INCL_FHR_FROM
    # A row is flagged if its footnote contains one of the texts; na=False: no footnote -> False, not empty
    footnote = table["Fussnote"]
    table["Area-wide values"] = footnote.str.contains("|".join(AREA_WIDE_FOOTNOTES), regex=True, na=False)
    table["Region"] = table["Land"].map(to_region)

    # The election year becomes the exact election day (ISO string), as in btw_results.csv
    table["Date"] = to_election_date(table["Jahr"])

    table = table.drop(columns="Jahr").rename(columns=RENAME)
    return table[OUTPUT_COLUMNS].sort_values(["Date", "Constituency number"]).reset_index(drop=True)


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------

def main() -> None:
    print(f"Reading structural data from {INPUT_FILE}")
    raw = pd.read_csv(INPUT_FILE)
    check_input(raw)

    result = build_table(raw)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT_FILE, index=False)

    # Short summary for the console
    print(f"Wrote {len(result)} rows to {OUTPUT_FILE}")
    print(f"Dropped columns: {', '.join(DROPPED_COLUMNS)}")
    summary = result.groupby("Date").agg(
        rows=("Constituency", "size"),
        area_wide=("Area-wide values", "sum"),
        abitur_incl_fhr=("Abitur incl. Fachhochschulreife", "all"),
    )
    print(summary.to_string())
    empty = result.isna().sum()
    print("Empty values per column:")
    print(empty[empty > 0].to_string())


if __name__ == "__main__":
    main()

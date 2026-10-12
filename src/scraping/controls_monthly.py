"""
Fetch two monthly control series for Germany from the Eurostat dissemination API.

Source : https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/<dataset>
  Free, no API key, JSON-stat 2.0 format. Both series start 2013-01 and run to
  the latest published month (the last 1-2 months of a request are usually
  still empty and are left NaN).

  une_rt_m         Unemployment rate, monthly. Filters: geo=DE, s_adj=SA
                   (seasonally adjusted), age=TOTAL, sex=T, unit=PC_ACT
                   (percent of the active population).
  migr_asyappctzm  Asylum applicants, monthly. Filters: geo=DE, citizen=TOTAL,
                   sex=T, age=TOTAL, unit=PER, applicant=FRST.

Why these measures (also in docs/decisions.md):
  * The Eurostat unemployment rate is the ILO-harmonised rate from the Labour
    Force Survey. It is about 2 points LOWER than the Bundesagentur fuer Arbeit
    "Arbeitslosenquote" quoted in German media (different definition of
    unemployed and of the labour force). We use it because it is freely
    downloadable as one long, consistent, seasonally adjusted monthly series.
  * `applicant=FRST` = first-time applicants, i.e. "Erstantraege". This is the
    figure the BAMF and German media report as new asylum applications. The
    dataset also has TOTAL (first-time + subsequent); subsequent applications
    are repeat claims by people already in Germany, so they do not measure new
    arrivals. Eurostat rounds counts to the nearest 5.
  * The series counts formal applications filed with the BAMF, which lagged
    the arrivals of autumn 2015 by months (registration backlog). The maximum
    is therefore 2016-08 (92,115), not autumn 2015 (~58,000 per month).

Raw    : data/raw/eurostat/<dataset>.json   (one JSON-stat response per dataset,
         reused if present; --force refetches)
Interim: data/interim/controls_monthly.csv, one row per month, sorted:
    Monat              YYYY-MM
    arbeitslosenquote  percent (float), NaN where not yet published
    asylantraege       count (float, because of NaN), NaN where not yet published

Run from the project root:
    python -m src.scraping.controls_monthly            # uses cached raw JSON if present
    python -m src.scraping.controls_monthly --force    # refetch from Eurostat
Needs `requests` and `pandas`.
"""

import argparse
import json
from pathlib import Path

import pandas as pd
import requests

BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}"
ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "eurostat"
OUT = ROOT / "data" / "interim" / "controls_monthly.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (DALAS student project)"}
SINCE = "2013-01"

# dataset -> (output column, filter parameters). Every non-time dimension is
# fixed to one code so the response is a single time series.
SERIES = {
    "une_rt_m": ("arbeitslosenquote",
                 {"geo": "DE", "s_adj": "SA", "age": "TOTAL", "sex": "T", "unit": "PC_ACT"}),
    "migr_asyappctzm": ("asylantraege",
                        {"geo": "DE", "citizen": "TOTAL", "sex": "T", "age": "TOTAL",
                         "unit": "PER", "applicant": "FRST"}),
}


def fetch(dataset: str, params: dict, force: bool = False) -> dict:
    """Return the JSON-stat response for one dataset, from cache unless --force."""
    raw = RAW_DIR / f"{dataset}.json"
    if raw.exists() and not force:
        return json.loads(raw.read_text(encoding="utf-8"))
    query = {"format": "JSON", "lang": "EN", "sinceTimePeriod": SINCE, **params}
    r = requests.get(BASE.format(dataset=dataset), params=query, headers=HEADERS, timeout=60)
    r.raise_for_status()
    data = r.json()
    if "error" in data:  # Eurostat returns 200 + "error" list for some bad filters
        raise ValueError(f"{dataset}: {data['error']}")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw.write_text(r.text, encoding="utf-8")
    print(f"fetched {r.url}")
    return data


def parse(data: dict, column: str) -> pd.Series:
    """Turn a JSON-stat response with a single free dimension (time) into a Series.

    JSON-stat stores the data cube flattened:
      * `id`   lists the dimensions in order, `size` their lengths.
      * `value` is a dict {flat_index (as string): number}; cells with no data
        are simply absent, which is how "not yet published" months show up.
      * The flat index is row-major over `id`, i.e. the LAST dimension (time)
        varies fastest. Because every other dimension has size 1 here, the
        flat index equals the time index, but we compute the stride anyway so
        a mistake in the filters (two codes in one dimension) raises instead of
        silently mixing series.
      * `dimension.time.category.index` maps "YYYY-MM" -> position along time.
    """
    dims, sizes = data["id"], data["size"]
    if dims[-1] != "time":
        raise ValueError(f"expected time as last dimension, got {dims}")
    if any(n != 1 for n in sizes[:-1]):
        raise ValueError(f"filters did not pin all dimensions to one code: {dict(zip(dims, sizes))}")
    time_index = data["dimension"]["time"]["category"]["index"]  # {"2013-01": 0, ...}
    values = data["value"]
    series = {month: values.get(str(pos)) for month, pos in time_index.items()}  # None = missing
    return pd.Series(series, name=column, dtype="float64").rename_axis("Monat")


def main(force: bool = False):
    parts = []
    for dataset, (column, params) in SERIES.items():
        parts.append(parse(fetch(dataset, params, force), column))
    df = pd.concat(parts, axis=1, join="outer").sort_index().reset_index()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False, float_format="%g", encoding="utf-8")
    print(f"{len(df)} months written to {OUT.relative_to(ROOT)}: {df['Monat'].iloc[0]} .. {df['Monat'].iloc[-1]}")

    # sanity checks: last published month per series, and the 2015/16 asylum peak
    for col in ["arbeitslosenquote", "asylantraege"]:
        last = df.loc[df[col].notna(), "Monat"].iloc[-1]
        print(f"  {col}: last published month {last}, {df[col].isna().sum()} months NaN")
    peak = df.set_index("Monat").loc[["2015-10", "2015-11", "2016-01"], "asylantraege"]
    print("  asylantraege 2015-10 / 2015-11 / 2016-01:", " / ".join(f"{v:g}" for v in peak))
    top = df.loc[df["asylantraege"].idxmax()]
    print(f"  asylantraege maximum: {top['asylantraege']:g} in {top['Monat']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--force", action="store_true", help="refetch from Eurostat even if the raw JSON exists")
    main(force=ap.parse_args().force)

"""
Scrape the 21st-century event table from taschenhirn.de.

Source : https://www.taschenhirn.de/geschichte/aktuelle-geschichte-des-21-jahrhunderts/
Raw    : data/raw/taschenhirn_21jh.html   (untouched HTML snapshot, for reproducibility)
Interim: data/interim/events_taschenhirn.csv with columns
    jahr        year or range as printed on the page ("2001", "2010-2012")
    jahr_start  first year as int
    jahr_ende   last year as int (== jahr_start for single years)
    ereignis    one event per row; multi-event cells split at paragraphs / line breaks

Run from the project root:  python -m src.scraping.taschenhirn
Only needs `requests`; parsing uses the standard library.
"""

import csv
import re
from html.parser import HTMLParser
from pathlib import Path

import requests

URL = "https://www.taschenhirn.de/geschichte/aktuelle-geschichte-des-21-jahrhunderts/"
ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "taschenhirn_21jh.html"
OUT = ROOT / "data" / "interim" / "events_taschenhirn.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (DALAS student project)"}

YEAR_RE = re.compile(r"^\s*(\d{4})\s*(?:[-–/]\s*(\d{4}))?\s*$")


class TableParser(HTMLParser):
    """Collects every <table> as rows of cells; a cell is a list of text
    fragments, split whenever a <br>, <p>, <li> or <div> starts."""

    def __init__(self):
        super().__init__()
        self.tables = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = [""]
        elif tag in ("br", "p", "li", "div") and self._cell is not None:
            self._cell.append("")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell[-1] += data


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def fetch(url: str = URL) -> str:
    html = requests.get(url, headers=HEADERS, timeout=30).text
    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(html, encoding="utf-8")
    return html


def parse(html: str) -> list[dict]:
    parser = TableParser()
    parser.feed(html)
    rows = []
    for table in parser.tables:
        for cells in table:
            if len(cells) < 2:
                continue
            year_text = clean(" ".join(cells[0]))
            m = YEAR_RE.match(year_text)
            if not m:  # header row or decade heading
                continue
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else start
            for frag in cells[1]:
                ev = clean(frag)
                if ev:
                    rows.append({"jahr": year_text, "jahr_start": start, "jahr_ende": end, "ereignis": ev})
    return rows


def main():
    rows = parse(fetch())
    if not rows:
        raise SystemExit("No rows found. Page layout may have changed; inspect data/raw/taschenhirn_21jh.html.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["jahr", "jahr_start", "jahr_ende", "ereignis"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} events written to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

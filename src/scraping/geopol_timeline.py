"""
Scrape the geopolitical event timeline from geopol.uk (secondary event source).

Source : https://geopol.uk/timeline/
  One page, ~124 events from 1648 to 2026, grouped into era sections
  (<section class="timeline-era">, heading = era title). Each event is a
  <div class="timeline-event"> with a <time> date line, a linked title (<h3>),
  a one-sentence description (<p>) and a category label (<span>).
  The prose sections on the page ("Year-by-Year Overview", "How Conflict
  Shaped the Modern World", FAQ) are not event entries and are not scraped.
Raw    : data/raw/geopol/timeline.html   (untouched snapshot; skipped if it exists)
Interim: data/interim/events_geopol.csv, one row per event with year >= 2013, columns
    quelle            "geopol"
    quelle_url        the event's own article link (absolute)
    quelle_permalink  empty (the site has no revision permalinks)
    abgerufen_am      fetch time, ISO; for a cached raw file the file's modification time
    jahr              year parsed from the date line
    abschnitt         era heading (e.g. "The New Disorder")
    unterabschnitt    category label (e.g. "Powers")
    verschachtelt     always 0 (the page has no nested entries)
    datum             YYYY-MM-DD (day), YYYY-MM-01 (month) or YYYY-01-01 (year only)
    datum_ende        end of a year range, YYYY-01-01; empty otherwise
    datum_praezision  tag | monat | jahr | fehlt
    datum_roh         date line as printed ("September 11, 2001", "March 2014", "2021 – 2022")
    text_roh          whole card text as printed, whitespace-normalised
    text              title + ". " + description
    stichworte        the event title
    stichworte_urls   the event's article link

Nothing is inferred: a date line that matches none of the patterns below
leaves datum empty with datum_praezision = "fehlt" (none occur for 2013+).
Rows whose year cannot be parsed from the date line are dropped with a warning,
because the year filter cannot be applied to them.

Run from the project root:  python3 -m src.scraping.geopol_timeline
Dependencies: requests, beautifulsoup4
"""

import csv
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = "https://geopol.uk/timeline/"
ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "geopol" / "timeline.html"
OUT = ROOT / "data" / "interim" / "events_geopol.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (DALAS student project)"}
MIN_YEAR = 2013  # AfD founding year; earlier entries are dropped

FIELDS = [
    "quelle", "quelle_url", "quelle_permalink", "abgerufen_am", "jahr",
    "abschnitt", "unterabschnitt", "verschachtelt", "datum", "datum_ende",
    "datum_praezision", "datum_roh", "text_roh", "text", "stichworte", "stichworte_urls",
]

# English month names as printed on the page -> month number
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
MONTH = "(" + "|".join(MONTHS) + ")"
DASH = r"\s*[-–—]\s*"  # hyphen, en dash, em dash, optional spaces around

# The whole date line must match one of these (case-insensitive).
DAY_RE = re.compile(rf"^{MONTH}\s+(\d{{1,2}}),\s*(\d{{4}})$", re.I)   # "September 11, 2001"
MONTH_RE = re.compile(rf"^{MONTH}\s+(\d{{4}})$", re.I)                # "March 2014"
YEAR_RE = re.compile(r"^(\d{4})$")                                    # "2019"
YEAR_RANGE_RE = re.compile(rf"^(\d{{4}}){DASH}(\d{{4}})$")             # "2021 – 2022"


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_date(raw: str) -> dict:
    """Map the printed date line to datum / datum_ende / datum_praezision / jahr.
    Unmatched strings (e.g. "1830s – 1907") give empty datum and praezision 'fehlt'."""
    out = {"datum": "", "datum_ende": "", "datum_praezision": "fehlt", "jahr": ""}
    if m := DAY_RE.match(raw):
        d = date(int(m[3]), MONTHS[m[1].lower()], int(m[2]))  # raises on an impossible day
        out.update(datum=d.isoformat(), datum_praezision="tag", jahr=str(d.year))
    elif m := MONTH_RE.match(raw):
        out.update(datum=f"{m[2]}-{MONTHS[m[1].lower()]:02d}-01", datum_praezision="monat", jahr=m[2])
    elif m := YEAR_RE.match(raw):
        out.update(datum=f"{m[1]}-01-01", datum_praezision="jahr", jahr=m[1])
    elif m := YEAR_RANGE_RE.match(raw):
        out.update(datum=f"{m[1]}-01-01", datum_ende=f"{m[2]}-01-01", datum_praezision="jahr", jahr=m[1])
    return out


def fetch() -> str:
    if not RAW.exists():  # cached download: never re-request an existing snapshot
        html = requests.get(URL, headers=HEADERS, timeout=30).text
        RAW.parent.mkdir(parents=True, exist_ok=True)
        RAW.write_text(html, encoding="utf-8")
    return RAW.read_text(encoding="utf-8")


def parse(html: str, fetched_at: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for era in soup.select("section.timeline-era"):
        era_title = era.select_one("h2.timeline-era-title")
        abschnitt = clean(era_title.get_text()) if era_title else ""
        for ev in era.select("div.timeline-event"):
            time_el = ev.select_one("time.timeline-event-date")
            link = ev.select_one("h3.timeline-event-title a")
            desc = ev.select_one("p.timeline-event-description")
            cat = ev.select_one("span.timeline-event-category")
            datum_roh = clean(time_el.get_text()) if time_el else ""
            d = parse_date(datum_roh)
            if not d["jahr"]:
                print(f"WARNING: no year parsed from {datum_roh!r} ({clean(ev.get_text())[:60]!r}); row dropped")
                continue
            # cross-check only: the page also carries the year as data-year
            if ev.get("data-year") and ev["data-year"] != d["jahr"]:
                print(f"NOTE: data-year={ev['data-year']} differs from parsed year {d['jahr']} for {datum_roh!r}")
            if int(d["jahr"]) < MIN_YEAR:
                continue
            title = clean(link.get_text()) if link else ""
            summary = clean(desc.get_text()) if desc else ""
            href = urljoin(URL, link["href"]) if link and link.get("href") else ""
            if not title or not summary or not href:
                print(f"WARNING: incomplete card for {datum_roh!r}: title={title!r} href={href!r}")
            rows.append({
                "quelle": "geopol",
                "quelle_url": href or URL,
                "quelle_permalink": "",
                "abgerufen_am": fetched_at,
                "jahr": d["jahr"],
                "abschnitt": abschnitt,
                "unterabschnitt": clean(cat.get_text()) if cat else "",
                "verschachtelt": 0,
                "datum": d["datum"],
                "datum_ende": d["datum_ende"],
                "datum_praezision": d["datum_praezision"],
                "datum_roh": datum_roh,
                "text_roh": clean(ev.get_text(" ")),
                "text": ". ".join(p for p in (title, summary) if p),
                "stichworte": title,
                "stichworte_urls": href,
            })
    return rows


def main():
    html = fetch()
    # the raw file's modification time is the moment it was downloaded
    fetched_at = datetime.fromtimestamp(RAW.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    rows = parse(html, fetched_at)
    if not rows:
        raise SystemExit("No rows found. Page layout may have changed; inspect data/raw/geopol/timeline.html.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} events written to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

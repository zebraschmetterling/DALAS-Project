"""
Scrape the English Wikipedia list "Crisis situations and unrest in Europe since 2000"
(secondary event source).

Source : https://en.wikipedia.org/wiki/Crisis_situations_and_unrest_in_Europe_since_2000
  Reverse-chronological, one <h2> section per year (id = the year), each with
  one or more bulleted lists. Bullets can be nested: a sub-event sits in a
  <ul> inside the <li> of its umbrella entry. Dates inside the bullets are
  mixed ("May 19–28", "4–10 February 2014", "February 24, 2022", "November
  2015", "2023–2024", "2023–present") and many bullets carry no date at all.
Raw    : data/raw/wikipedia_crises_en/page.html  (untouched snapshot; skipped if it exists)
Interim: data/interim/events_wikipedia_crises_en.csv, one row per bullet in the
  year sections 2013 and later (nested bullets become their own rows), columns
    quelle            "wikipedia_en_crises"
    quelle_url        page URL + "#<year>" (the year section's anchor)
    quelle_permalink  https://en.wikipedia.org/w/index.php?title=...&oldid=<wgRevisionId>
                      (revision id read from the page's JS config; empty if not found)
    abgerufen_am      fetch time, ISO; for a cached raw file the file's modification time
    jahr              year of the parsed date token; the section year if the token has
                      no year ("May 19–28") or there is no token
    abschnitt         year heading of the section the bullet sits in
    unterabschnitt    text of the umbrella bullet(s) for nested rows, outer to inner,
                      joined with " > "; empty for top-level bullets
    verschachtelt     1 for nested bullets, 0 for top-level bullets
    datum             YYYY-MM-DD (day), YYYY-MM-01 (month), YYYY-01-01 (year only)
    datum_ende        end of a range in the same precision; empty for "present"
    datum_praezision  tag | monat | jahr | fehlt (no date token in the bullet)
    datum_roh         the date token as printed
    text_roh          bullet text as printed (own text only, not the nested bullets),
                      whitespace-normalised, footnote markers like [1] kept
    text              text_roh without the date token and without footnote markers
    stichworte        link titles (Wikipedia `title` attribute, else link text), "|"-separated
    stichworte_urls   the matching absolute link URLs, "|"-separated

Date rule: the most precise token wins (day > month > year); among equally
precise tokens the first one in the bullet. Nothing is inferred: a bullet
without a date token gets datum = "" and datum_praezision = "fehlt"; the year
then comes from the section heading only. Titles like "2024 Harehills riot"
are a year-only date token, so `text` loses the leading year while
`stichworte` keeps the full link title.

The page has no "2016" section (as of the scraped revision), so no 2016 rows exist.
Sections are filtered by their heading (>= 2013); an entry in the 2012 section
whose range reaches into 2013 would be dropped.

Run from the project root:  python3 -m src.scraping.wikipedia_crises_en
Dependencies: requests, beautifulsoup4
"""

import csv
import re
from copy import copy
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URL = "https://en.wikipedia.org/wiki/Crisis_situations_and_unrest_in_Europe_since_2000"
PAGE_TITLE = URL.rsplit("/", 1)[1]
PERMALINK = "https://en.wikipedia.org/w/index.php?title={title}&oldid={oldid}"
ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "wikipedia_crises_en" / "page.html"
OUT = ROOT / "data" / "interim" / "events_wikipedia_crises_en.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (DALAS student project)"}
MIN_YEAR = 2013  # AfD founding year; earlier year sections are dropped

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
MONTH = "(?:" + "|".join(MONTHS) + ")"
DASH = r"\s*[-–—]\s*"  # hyphen, en dash, em dash, optional spaces around
YEAR = r"(?:1\d{3}|20\d{2})"     # four-digit year 1000-2099 (keeps "COVID-19" out)
DAY = r"\d{1,2}"

REVISION_RE = re.compile(r'"wgRevisionId"\s*:\s*(\d+)')

# Date tokens searched inside a bullet, most precise first. Named groups:
#   d1/d2 = start/end day, mon = month name, y = year (optional in DAY_NOYEAR_RE)
DAY_DMY_RE = re.compile(                        # "4–10 February 2014", "24 February 2022"
    rf"\b(?P<d1>{DAY})(?:{DASH}(?P<d2>{DAY}))?\s+(?P<mon>{MONTH})\s+(?P<y>{YEAR})\b", re.I)
DAY_MDY_RE = re.compile(                        # "February 24, 2022", "May 19–28, 2013"
    rf"\b(?P<mon>{MONTH})\s+(?P<d1>{DAY})(?:{DASH}(?P<d2>{DAY}))?,\s*(?P<y>{YEAR})\b", re.I)
DAY_NOYEAR_RE = re.compile(                     # "May 19–28"  (year taken from the section)
    rf"\b(?P<mon>{MONTH})\s+(?P<d1>{DAY})(?:{DASH}(?P<d2>{DAY}))?\b(?!,?\s*{YEAR})", re.I)
MONTH_RE = re.compile(                          # "November 2015"
    rf"\b(?P<mon>{MONTH})\s+(?P<y>{YEAR})\b", re.I)
YEAR_RE = re.compile(                           # "2024", "2022 – 2024", "2023–present"
    rf"\b(?P<y>{YEAR})\b(?:{DASH}(?:(?P<y2>{YEAR})\b|(?P<present>present)\b))?", re.I)

EMPTY_PARENS_RE = re.compile(r"\(\s*\)")        # left behind when a "(date)" token is removed
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:)])")


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_date(text: str, section_year: str) -> dict:
    """Find the most precise date token in `text`. Returns datum, datum_ende,
    datum_praezision, datum_roh, jahr; a text without a token gives praezision 'fehlt'."""
    out = {"datum": "", "datum_ende": "", "datum_praezision": "fehlt", "datum_roh": "", "jahr": section_year}
    for rx in (DAY_DMY_RE, DAY_MDY_RE, DAY_NOYEAR_RE):
        if m := rx.search(text):
            year = int(m["y"]) if "y" in m.groupdict() and m["y"] else int(section_year)
            mon = MONTHS[m["mon"].lower()]
            start = date(year, mon, int(m["d1"]))  # raises on an impossible day -> no silent fix
            out.update(datum=start.isoformat(), datum_praezision="tag", datum_roh=m.group(0), jahr=str(year))
            if m["d2"]:
                out["datum_ende"] = date(year, mon, int(m["d2"])).isoformat()
            return out
    if m := MONTH_RE.search(text):
        out.update(datum=f"{m['y']}-{MONTHS[m['mon'].lower()]:02d}-01", datum_praezision="monat",
                   datum_roh=m.group(0), jahr=m["y"])
        return out
    if m := YEAR_RE.search(text):
        out.update(datum=f"{m['y']}-01-01", datum_praezision="jahr", datum_roh=m.group(0), jahr=m["y"])
        if m["y2"]:
            out["datum_ende"] = f"{m['y2']}-01-01"
        return out
    return out


def own_copy(li, drop_refs: bool):
    """Copy of the <li> without its nested lists (and without footnote markers if asked),
    so text and links belong to this bullet only."""
    node = copy(li)
    for ul in node.find_all(["ul", "ol"]):
        ul.decompose()
    if drop_refs:
        for sup in node.select("sup.reference"):
            sup.decompose()
    return node


def fetch() -> str:
    if not RAW.exists():  # cached download: never re-request an existing snapshot
        html = requests.get(URL, headers=HEADERS, timeout=30).text
        RAW.parent.mkdir(parents=True, exist_ok=True)
        RAW.write_text(html, encoding="utf-8")
    return RAW.read_text(encoding="utf-8")


def parse(html: str, fetched_at: str) -> list[dict]:
    m = REVISION_RE.search(html)
    permalink = PERMALINK.format(title=PAGE_TITLE, oldid=m[1]) if m else ""
    if not permalink:
        print("WARNING: wgRevisionId not found; quelle_permalink left empty")
    soup = BeautifulSoup(html, "html.parser")
    content = soup.select_one("div.mw-parser-output")
    rows = []

    def walk(ul, section_year, parents):
        for li in ul.find_all("li", recursive=False):
            with_refs = own_copy(li, drop_refs=False)
            no_refs = own_copy(li, drop_refs=True)
            text_roh = clean(with_refs.get_text())
            text_clean = clean(no_refs.get_text())
            d = parse_date(text_clean, section_year)
            if d["datum_roh"]:  # remove the date token, then tidy what it leaves behind
                text_clean = text_clean.replace(d["datum_roh"], "", 1)
                text_clean = EMPTY_PARENS_RE.sub("", text_clean)
                text_clean = clean(SPACE_BEFORE_PUNCT_RE.sub(r"\1", text_clean))
            links = no_refs.find_all("a", href=True)
            rows.append({
                "quelle": "wikipedia_en_crises",
                "quelle_url": f"{URL}#{section_year}",
                "quelle_permalink": permalink,
                "abgerufen_am": fetched_at,
                "jahr": d["jahr"],
                "abschnitt": section_year,
                "unterabschnitt": " > ".join(parents),
                "verschachtelt": 1 if parents else 0,
                "datum": d["datum"],
                "datum_ende": d["datum_ende"],
                "datum_praezision": d["datum_praezision"],
                "datum_roh": d["datum_roh"],
                "text_roh": text_roh,
                "text": text_clean,
                "stichworte": "|".join(clean(a.get("title") or a.get_text()) for a in links),
                "stichworte_urls": "|".join(urljoin(URL, a["href"]) for a in links),
            })
            for sub in li.find_all(["ul", "ol"], recursive=False):
                walk(sub, section_year, parents + [text_roh])

    for h2 in content.find_all("h2"):
        section_year = clean(h2.get_text())
        if not section_year.isdigit() or int(section_year) < MIN_YEAR:
            continue  # "See also", "References", and years before the cut
        # the lists of a section are the siblings between this heading and the next one
        sib = h2.parent if "mw-heading" in (h2.parent.get("class") or []) else h2
        while (sib := sib.find_next_sibling()) is not None:
            if sib.name == "h2" or (sib.name == "div" and "mw-heading" in (sib.get("class") or [])):
                break
            if sib.name in ("ul", "ol"):
                walk(sib, section_year, [])
            else:
                print(f"NOTE: non-list element in section {section_year} skipped: <{sib.name}> {clean(sib.get_text())[:60]!r}")
    return rows


def main():
    html = fetch()
    # the raw file's modification time is the moment it was downloaded
    fetched_at = datetime.fromtimestamp(RAW.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    rows = parse(html, fetched_at)
    if not rows:
        raise SystemExit("No rows found. Page layout may have changed; inspect data/raw/wikipedia_crises_en/page.html.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} entries written to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

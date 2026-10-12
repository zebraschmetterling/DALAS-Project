"""
Scrape the dated event lists from the German Wikipedia year articles.

Source : https://de.wikipedia.org/wiki/<year>  for 2013 .. 2026
         Only the "Ereignisse" section is read, and within it only the
         subsections listed in KEEP_SECTIONS (politics, economy, disasters)
         including their sub-subsections (e.g. "Terroranschläge",
         "Naturkatastrophen"). Sport, Kultur, Religion, Wissenschaft, ... are
         skipped on purpose; see docs/decisions.md.
Raw    : data/raw/wikipedia_jahr/<year>.html   (untouched snapshot; reused if
         present, refetched with --force)
Interim: data/interim/events_wikipedia_jahr.csv, one row per list item:
    quelle            "wikipedia_de_jahr"
    quelle_url        year page URL + anchor of the (innermost) heading the item sits in
    quelle_permalink  https://de.wikipedia.org/w/index.php?title=<year>&oldid=<revid>
                      (revision id read from the HTML; empty if not found)
    abgerufen_am      fetch timestamp (ISO, mtime of the raw file)
    jahr              page year
    abschnitt         h3 heading text (e.g. "Politik und Weltgeschehen")
    unterabschnitt    h4/h5 heading text under abschnitt, empty if none
    verschachtelt     1 if the <li> is nested under another <li>, else 0
    datum             YYYY-MM-DD; start day of a range; 1st of the month for
                      month-only tokens; EMPTY if no date token was found
    datum_ende        YYYY-MM-DD end of a range ("7.–9. Mai", "August/September"),
                      else empty
    datum_praezision  tag | monat | fehlt
    datum_roh         the raw date token as matched, so the parse can be checked
    text_roh          item text exactly as in the HTML (whitespace-normalised only)
    text              text_roh without the leading date token and footnote markers
    stichworte        titles of the Wikipedia article links in the item (title
                      attribute, else link text), "|"-separated, in order,
                      deduplicated; footnote links and red links (articles that
                      do not exist) are not included
    stichworte_urls   the corresponding link URLs, same order

Nothing is inferred: the date is parsed only from the start of the item's own
text (patterns in DATE_PATTERNS; day tokens with or without a following colon,
month-only tokens only with a colon). A nested item without its own date token
inherits the date of its parent item (verschachtelt = 1, datum_roh = parent's
token); an item without any date token gets datum = "" and datum_praezision =
"fehlt" (e.g. "Sommer 2022:", "Siehe auch: ...", "vermutlich 18. Juni:"). The
hidden zero-padding Wikipedia uses to align the lists ("01. Januar", "000Juli")
is part of the HTML text and therefore kept in text_roh and datum_roh.
Known quirk: in 2013 / Wirtschaft the items under "1. Januar:" are written as
an indented list (wikitext ":*"), which is a sibling <dl><dd><ul>, not a nested
list, so they get "fehlt" instead of inheriting the date. Not patched.

Run from the project root:
    python3 -m src.scraping.wikipedia_jahr            # uses cached raw HTML
    python3 -m src.scraping.wikipedia_jahr --force    # refetch all year pages
Needs `requests` and `beautifulsoup4`.
"""

import argparse
import csv
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString

BASE = "https://de.wikipedia.org"
YEARS = range(2013, 2027)
ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "wikipedia_jahr"
OUT = ROOT / "data" / "interim" / "events_wikipedia_jahr.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (DALAS student project)"}
SLEEP_S = 1.0

# h2 that holds the event lists; everything outside it is ignored
EVENTS_HEADING = "Ereignisse"
# h3 subsections of "Ereignisse" that are kept, with all their sub-subsections
# (h4/h5, e.g. "Terroranschläge (Auswahl)", "Naturkatastrophen", "D-A-CH").
# A heading matches if its text equals an entry or starts with "<entry> (".
# The title of the same section differs between years, so every variant that
# occurs on the 2013-2026 pages is listed explicitly:
#   Wirtschaft                 2013-2016, 2021, 2025, 2026
#   Wirtschaft und Infrastruktur  2017, 2018
#   Katastrophen               2013-2020 (with h4 Naturkatastrophen / Schwere Unglücksfälle)
#   Wetter und Katastrophen    2021-2026
#   Schwere Unglücksfälle      2022-2026 as its own h3 (before: h4 under Katastrophen)
#   Anschläge                  2021 as its own h3 (before: h4 Terroranschläge under Politik)
# Not kept although related: "Soziales" (2013), "Gesellschaft, Kultur, Wirtschaft"
# (2020, mixed with culture), "Bereits feststehende Ereignisse" (2024-2025, scheduled).
KEEP_SECTIONS = [
    "Politik und Weltgeschehen",
    "Terroranschläge", "Anschläge",
    "Wirtschaft", "Wirtschaft und Infrastruktur",
    "Katastrophen", "Wetter und Katastrophen", "Schwere Unglücksfälle",
]

FIELDS = [
    "quelle", "quelle_url", "quelle_permalink", "abgerufen_am", "jahr",
    "abschnitt", "unterabschnitt", "verschachtelt",
    "datum", "datum_ende", "datum_praezision", "datum_roh",
    "text_roh", "text", "stichworte", "stichworte_urls",
]

# ---------------------------------------------------------------- date parsing
MONTHS = {
    "Januar": 1, "Februar": 2, "März": 3, "April": 4, "Mai": 5, "Juni": 6,
    "Juli": 7, "August": 8, "September": 9, "Oktober": 10, "November": 11, "Dezember": 12,
}
M = r"(?:" + "|".join(MONTHS) + r")"          # one month name
D = r"\d{1,2}\."                               # one day number with trailing dot ("7.", "01.")
# range / enumeration separator: en dash, hyphen, slash, "bis", "und", "&"
# ("7.–9. Mai", "14./15. Juli", "23. April und 7. Mai" -> datum = first, datum_ende = second)
SEP = r"\s*(?:–|-|/|bis|und|&)\s*"
# optional vague prefix words ("Ab August", "seit dem 31. Januar", "Ab Mitte März");
# they do not change the parsed date, the precision stays tag / monat
PREFIX = r"(?:(?:[Aa]b|[Ss]eit|[Aa]nfang|[Mm]itte|[Ee]nde|[Bb]is|dem)\s+)*"
YEAR = r"(?:\s+(\d{4}))?"                      # optional explicit year ("Dezember 2019")
# Wikipedia pads the lists with hidden zeros ("{{0}}") so that "1. Januar" and
# "Juli" line up with "15. Januar"; in the text they appear as "01. Januar" / "000Juli".
PAD = r"0*"

# All patterns are anchored at the start of the item text. Day tokens are
# unambiguous, so the colon after them is optional ("Ab 17. Juli 2021 kommt es");
# month-only tokens must be followed by a colon ("August:", "Ab August:") because
# a bare month name at the start of a sentence is not necessarily a date.
# Group layout is fixed per pattern so the parse below can stay simple.
DATE_PATTERNS = [
    # "28. Februar – 3. März[ 2015]"           d1 m1 d2 m2 year
    ("tag", "d1 m1 d2 m2 y", re.compile(
        rf"^{PAD}{PREFIX}({D})\s*({M}){SEP}({D})\s*({M}){YEAR}\s*:?")),
    # "14./15. Juli", "7.–9. Mai", "16. – 25. Januar"[ 2015]   d1 d2 m year
    ("tag", "d1 d2 m y", re.compile(
        rf"^{PAD}{PREFIX}({D}){SEP}({D})\s*({M}){YEAR}\s*:?")),
    # "15. April[ 2015]"                                       d1 m year
    ("tag", "d1 m y", re.compile(
        rf"^{PAD}{PREFIX}({D})\s*({M}){YEAR}\s*:?")),
    # "August/September", "Januar bis März"[ 2015]             m1 m2 year
    ("monat", "m1 m2 y", re.compile(
        rf"^{PAD}{PREFIX}({M}){SEP}({M}){YEAR}\s*:")),
    # "August", "Ab August", "Dezember 2019"                   m year
    ("monat", "m y", re.compile(
        rf"^{PAD}{PREFIX}({M}){YEAR}\s*:")),
]


def iso(year, month, day):
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_date(text, page_year):
    """Return (datum, datum_ende, praezision, datum_roh) from the start of `text`.
    Dates are taken literally; only the year is filled from the page when the
    token has none (the article is the year's article). Impossible dates such
    as "31. Februar" are kept as the raw token but datum stays empty."""
    for praezision, layout, rx in DATE_PATTERNS:
        m = rx.match(text)
        if not m:
            continue
        g = m.groups()
        roh = m.group(0).rstrip(":").strip()
        try:
            if layout == "d1 m1 d2 m2 y":
                # an explicit year at the end belongs to the end date
                # ("31. Dezember / 1. Januar 2016" on the 2015 page); the
                # start date is in the page year
                y_end = int(g[4]) if g[4] else page_year
                start = iso(page_year, MONTHS[g[1]], int(g[0].rstrip(".")))
                end = iso(y_end, MONTHS[g[3]], int(g[2].rstrip(".")))
            elif layout == "d1 d2 m y":
                y = int(g[3]) if g[3] else page_year
                start = iso(y, MONTHS[g[2]], int(g[0].rstrip(".")))
                end = iso(y, MONTHS[g[2]], int(g[1].rstrip(".")))
            elif layout == "d1 m y":
                y = int(g[2]) if g[2] else page_year
                start = iso(y, MONTHS[g[1]], int(g[0].rstrip(".")))
                end = ""
            elif layout == "m1 m2 y":
                y = int(g[2]) if g[2] else page_year
                start = iso(y, MONTHS[g[0]], 1)
                end = iso(y, MONTHS[g[1]], 1)
            else:  # "m y"
                y = int(g[1]) if g[1] else page_year
                start = iso(y, MONTHS[g[0]], 1)
                end = ""
            # datetime validates day-of-month; raises for e.g. "30. Februar"
            datetime.strptime(start, "%Y-%m-%d")
            if end:
                datetime.strptime(end, "%Y-%m-%d")
        except ValueError:
            return "", "", "fehlt", roh
        return start, end, praezision, roh
    return "", "", "fehlt", ""


# ---------------------------------------------------------------- fetching
def fetch(year, force=False):
    """Download the year page to data/raw unless cached. Returns (html, fetched_at)."""
    path = RAW_DIR / f"{year}.html"
    if force or not path.exists():
        r = requests.get(f"{BASE}/wiki/{year}", headers=HEADERS, timeout=30)
        r.raise_for_status()
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(r.text, encoding="utf-8")
        time.sleep(SLEEP_S)
    fetched_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    return path.read_text(encoding="utf-8"), fetched_at


def revision_id(html):
    """Revision id from mw.config ("wgRevisionId":123) or the permanent link (oldid=123)."""
    m = re.search(r'"wgRevisionId":\s*(\d+)', html) or re.search(r"[?&]oldid=(\d+)", html)
    return m.group(1) if m else ""


# ---------------------------------------------------------------- parsing
def clean(text):
    return re.sub(r"\s+", " ", text).strip()


def heading_text(h):
    return clean(h.get_text(" "))


def heading_matches(text, names):
    return any(text == n or text.startswith(n + " (") for n in names)


def own_children(li):
    """Direct children of an <li> except nested lists (their items get own rows)."""
    return [c for c in li.children if not (getattr(c, "name", None) in ("ul", "ol"))]


def node_text(nodes, drop_refs):
    """Plain text of the nodes; optionally without footnote <sup class="reference">."""
    parts = []
    for c in nodes:
        if isinstance(c, NavigableString):
            parts.append(str(c))
        elif c.name == "sup" and drop_refs and "reference" in (c.get("class") or []):
            continue
        else:
            if drop_refs:
                for sup in c.find_all("sup", class_="reference"):
                    sup.decompose()
            parts.append(c.get_text())
    return clean("".join(parts))


def item_links(li):
    """(title, url) of the Wikipedia article links in the item's own text,
    in order, deduplicated. Footnote links and links in nested lists are skipped."""
    titles, urls = [], []
    for a in li.find_all("a", href=True):
        # walk up to the <li>; skip if a nested list or a footnote sup is in between
        p = a.parent
        skip = False
        while p is not None and p is not li:
            if p.name in ("ul", "ol", "sup"):
                skip = True
                break
            p = p.parent
        if skip:
            continue
        href = a["href"]
        # only article links: /wiki/<Title>; external links and edit/cite links are not keywords
        if "/wiki/" not in href or ":" in href.split("/wiki/", 1)[1]:
            continue
        # red links (class "new") point to a non-existing article (edit URL with
        # redlink=1, title "... (Seite nicht vorhanden)"); they are not keywords
        if "new" in (a.get("class") or []) or "redlink=1" in href:
            continue
        title = clean(a.get("title") or a.get_text())
        if not title or title in titles:
            continue
        titles.append(title)
        urls.append(urljoin(BASE, href))
    return titles, urls


def walk_items(lst, ctx, parent_date, nested, rows):
    """Recursively turn the <li> of a list into rows. `parent_date` is the
    (datum, datum_ende, praezision, datum_roh) tuple nested items inherit."""
    for li in lst.find_all("li", recursive=False):
        own = own_children(li)
        text_roh = node_text(own, drop_refs=False)
        # copy before decomposing footnotes so text_roh stays untouched
        own_copy = own_children(BeautifulSoup(str(li), "html.parser").li)
        text_clean = node_text(own_copy, drop_refs=True)
        date = parse_date(text_roh, ctx["jahr"])
        if date[3]:                       # own token found: strip it from the text
            text_clean = clean(re.sub(r"^" + re.escape(date[3]) + r"\s*:?", "", text_clean))
        elif nested and parent_date[2] != "fehlt":
            date = parent_date               # inherit from the dated parent
        titles, urls = item_links(li)
        rows.append({
            **ctx,
            "verschachtelt": int(nested),
            "datum": date[0], "datum_ende": date[1], "datum_praezision": date[2], "datum_roh": date[3],
            "text_roh": text_roh, "text": text_clean,
            "stichworte": "|".join(titles), "stichworte_urls": "|".join(urls),
        })
        for sub in li.find_all(["ul", "ol"], recursive=False):
            walk_items(sub, ctx, date, True, rows)


def parse(html, year, fetched_at):
    """Walk headings and lists in document order; keep lists under kept h3s inside Ereignisse."""
    soup = BeautifulSoup(html, "html.parser")
    permalink = ""
    rev = revision_id(html)
    if rev:
        permalink = f"{BASE}/w/index.php?title={year}&oldid={rev}"

    rows = []
    in_events = False     # inside the "Ereignisse" h2
    keep = False          # inside a kept h3 (or its sub-headings)
    abschnitt, unterabschnitt, anchor = "", "", ""
    seen_sections = []
    for el in soup.find_all(["h2", "h3", "h4", "h5", "h6", "ul", "ol"]):
        if el.name == "h2":
            in_events = heading_text(el) == EVENTS_HEADING
            keep = False
            continue
        if not in_events:
            continue
        if el.name == "h3":
            abschnitt = heading_text(el)
            unterabschnitt = ""
            anchor = el.get("id", "")
            keep = heading_matches(abschnitt, KEEP_SECTIONS)
            if keep:
                seen_sections.append(abschnitt)
            continue
        if el.name in ("h4", "h5", "h6"):
            unterabschnitt = heading_text(el)
            anchor = el.get("id", anchor)
            if keep:
                seen_sections.append(unterabschnitt)
            continue
        # a list: only top-level ones (nested lists are handled by walk_items)
        if not keep or el.find_parent("li") is not None:
            continue
        # image galleries are <ul class="gallery">; their items are captions, not events
        if "gallery" in (el.get("class") or []):
            continue
        ctx = {
            "quelle": "wikipedia_de_jahr",
            "quelle_url": f"{BASE}/wiki/{year}#{anchor}" if anchor else f"{BASE}/wiki/{year}",
            "quelle_permalink": permalink,
            "abgerufen_am": fetched_at,
            "jahr": year,
            "abschnitt": abschnitt,
            "unterabschnitt": unterabschnitt,
        }
        walk_items(el, ctx, ("", "", "fehlt", ""), False, rows)
    return rows, seen_sections, rev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="refetch pages even if cached")
    args = ap.parse_args()

    all_rows = []
    for year in YEARS:
        html, fetched_at = fetch(year, force=args.force)
        rows, sections, rev = parse(html, year, fetched_at)
        n_fehlt = sum(r["datum_praezision"] == "fehlt" for r in rows)
        missing = [s for s in KEEP_SECTIONS if not any(heading_matches(x, [s]) for x in sections)]
        note = ""
        if not rev:
            note += "  WARNING: no revision id found"
        if missing:
            note += f"  sections not found: {missing}"
        print(f"{year}: {len(rows):4d} rows, {n_fehlt:3d} without date, sections {sections}{note}")
        all_rows.extend(rows)

    if not all_rows:
        raise SystemExit("No rows found. Page layout may have changed; inspect data/raw/wikipedia_jahr/.")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"{len(all_rows)} rows written to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

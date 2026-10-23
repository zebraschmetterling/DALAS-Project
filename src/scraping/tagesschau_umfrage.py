"""
Scrape infratest dimap exit-poll charts from the tagesschau.de election archive.

The charts are images, but each <img> carries the data in its alt text. The format changed over time:
  2025:       "Umfrage, Bundestagswahl 2025, SPD-Stimmanteile in Altersgruppen im Vergleich zu 2021
               | Alle 16,0 (26,0) | 18 - 24 Jahre 12,0 (15,0) | ... | Infratest-dimap. 24.02.2025, 02:36 Uhr"
  2013-2021:  "AfD-Stimmanteile nach Altersgruppen, in % (Vgl. zu 2013): 18 - 24 Jahre 10, 25 - 34 Jahre 14,
               ..., Quelle: Infratest dimap"
              and a nested variant with groups separated by ";":
              "Wahlverhalten nach Geschlecht (in %) CDU/CSU: Männer 30, Frauen 36; SPD: Männer 20, ...; Quelle: ..."
              which we flatten to category "CDU/CSU: Männer".
  Only 2025 prints the previous election's value in brackets, so value_prev is empty before 2025. Some
  2025 charts print the signed change instead ("SPD 15,0 (-10,0)"); there value_prev = value - change.
  Some 2025 charts also hold two groups per party ("Union [Einfache Bildung] 32,0 ([Hohe Bildung] 27,0)"),
  which become two rows with category "Union: Einfache Bildung" / "Union: Hohe Bildung".
  Also, the 2021 pages show only a generic alt ("Wen wählten Jüngere und Ältere?"); the alt with the
  numbers sits on a sub-page per chart (charts/{page}/chart_{id}.shtml), which we fetch as well.
We parse all of this into long format: one row per chart x category.

URL pattern (same for all elections): https://www.tagesschau.de/wahl/archiv/{election}/{page}.shtml
  election  2025-02-23-BT-DE, 2021-09-26-BT-DE, 2017-09-24-BT-DE, 2013-09-22-BT-DE
  page      umfrage-alter (age), umfrage-job (occupation), umfrage-werwas ("Wer wählte was": gender,
            education, workers, first-time voters), umfrage-afd (AfD-specific; in 2013 this URL serves the
            general "Aktuelle Themen" charts, which include one chart on the AfD).
  There are no umfrage-geschlecht / umfrage-bildung pages (404); gender and education are on umfrage-werwas.
Every page also embeds teaser charts of other pages (folders charts/inc-weitere-umfragen-in-index/,
0000-TIMELINES/); we keep only images from the page's own folder charts/{page}/ so charts are not repeated
across the per-page CSVs.

Raw    : data/raw/tagesschau/{election}_{page}.html  (+ {election}_{page}_chart_{id}.html for 2021 sub-pages)
Interim: data/interim/tagesschau_{page}.csv  (all elections requested)
Columns: election, page, chart, category, value, value_prev, source, timestamp

Known gaps (kept as in the source, not fixed): three alt texts have no numbers at the source and are skipped
(2017 umfrage-afd "AfD-Wähler: Sehr wichtig für meine Wahlentscheidung", truncated; 2021 umfrage-afd "Ansichten
der AfD-Wählenden über eigene Partei", empty; 2013 umfrage-afd "Ansichten über Peer Steinbrück", truncated).
Some charts repeat a category label (2017 "AfD-Stimmanteile nach Geschlecht": "Männer 13, Frauen 8, Männer 25,
Frauen 16") because the alt text does not say which sub-group each pair belongs to. The 2025 source writes
"+10003,0" as change for parties without a 2021 value (BSW); value_prev is left empty there.

Run from the project root:  python -m src.scraping.tagesschau_umfrage
Only needs `requests`.
"""

import csv
import re
import time
from html import unescape
from pathlib import Path

import requests

SITE = "https://www.tagesschau.de"
BASE = SITE + "/wahl/archiv/{election}/{page}.shtml"
SUBPAGE = SITE + "/wahl/archiv/{election}/charts/{page}/chart_{chart_id}.shtml"
ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "tagesschau"
INTERIM_DIR = ROOT / "data" / "interim"
HEADERS = {"User-Agent": "Mozilla/5.0 (DALAS student project)"}
FIELDS = ["election", "page", "chart", "category", "value", "value_prev", "source", "timestamp"]

# what to scrape by default; extend as needed
ELECTIONS = ["2025-02-23-BT-DE", "2021-09-26-BT-DE", "2017-09-24-BT-DE", "2013-09-22-BT-DE"]
# umfrage-job (occupation) is scraped no more: dropped from the project on 2026-10-23, see docs/decisions.md.
# Pass pages=[..., "umfrage-job"] to main() to fetch it again.
PAGES = ["umfrage-alter", "umfrage-werwas", "umfrage-afd"]

IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.S)
CHART_ID_RE = re.compile(r"chart_(\d+)\.(?:jpg|png)$")
# 2025 items come in three shapes:
#   "18 - 24 Jahre 12,0 (15,0)"                      -> value, previous value in brackets
#   "SPD 15,0 (-10,0)", "AfD 39,0 ([Vgl. 2021] +20,0)" -> brackets hold the signed change since 2021 (or "±0")
#   "Union [Einfache Bildung] 32,0 ([Hohe Bildung] 27,0)" -> two groups per party, no previous value
ITEM_RE = re.compile(r"^(?P<cat>.+?)(?:\s+\[(?P<lab>[^\]]+)\])?\s+(?P<val>-?\d+(?:,\d+)?)"
                     r"(?:\s*\((?:\[(?P<lab2>[^\]]+)\]\s*)?(?P<prev>[+\-±]?\d+(?:,\d+)?)\))?$")
# 2013-2021 items: "18 - 24 Jahre 10, 25 - 34 Jahre 14" or "CDU 18,9; CSU 5,2". A comma is a decimal comma
# only if a digit follows directly; otherwise it (or a semicolon) separates items. Categories may contain commas.
OLD_ITEM_RE = re.compile(r"(?P<cat>.+?)\s+(?P<val>-?\d+(?:,\d+)?)(?:\s*[,;]\s+|\s*$)")
OLD_SOURCE_RE = re.compile(r"\s*[,;]\s*Quelle:\s*(?P<source>.+)$")
# "Title, in % (Vgl. zu 2013): data" / "Title, in %, Vgl. 2017: data" / "Title, in %: data"
OLD_HEAD_RE = re.compile(r"^(?P<title>.+?),? in %(?P<vgl>.*?): (?P<data>.+)$", re.S)


def fetch(url: str, raw_name: str) -> str:
    """Download once; the raw HTML is cached in data/raw/tagesschau/ and reused on later runs."""
    raw = RAW_DIR / raw_name
    if raw.exists():
        return raw.read_text(encoding="utf-8")
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    # The server answers "content-type: text/html" without a charset, so requests falls back to
    # ISO-8859-1 for r.text and "Grünen" becomes "GrÃ¼nen". Every archive page declares
    # <meta charset="utf-8">, so we decode the bytes as UTF-8 ourselves.
    text = r.content.decode("utf-8")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw.write_text(text, encoding="utf-8")
    time.sleep(0.5)  # be polite to the server
    return text


def attr(tag: str, name: str) -> str:
    m = re.search(rf'\b{name}="([^"]*)"', tag)
    return unescape(m[1]) if m else ""


def is_data(alt: str) -> bool:
    """Alt texts that carry numbers: 2025 start with 'Umfrage,', older ones end with 'Quelle: ...'."""
    return alt.startswith("Umfrage,") or "Quelle:" in alt


def chart_imgs(html: str, page: str) -> dict[str, str]:
    """Chart id -> alt text for the images that belong to this page.

    Only images from the page's own folder charts/{page}/ are kept (teaser charts of other pages live in
    other folders). On the 2021 sub-pages the src is just "chart_{id}.jpg" (no folder). The same image
    appears twice (<picture> and <noscript>), so we keep one alt per id, preferring one with data.
    """
    out: dict[str, str] = {}
    for tag in IMG_TAG_RE.findall(html):
        src = attr(tag, "src")
        m = CHART_ID_RE.search(src)
        if m and (f"charts/{page}/" in src or "/" not in src):
            alt = attr(tag, "alt")
            if m[1] not in out or (is_data(alt) and not is_data(out[m[1]])):
                out[m[1]] = alt
    return out


def to_float(s: str | None) -> float | None:
    return None if s is None else float(s.replace(",", "."))


def parse_alt_2025(alt: str) -> tuple[str, list[dict], str, str]:
    """Split a 2025 alt text ('... | cat val (prev) | ... | Infratest-dimap. <timestamp>')."""
    parts = [p.strip() for p in alt.split("|")]
    head = parts[0]                      # "Umfrage, Bundestagswahl 2025, <chart title>"
    chart = head.split(",", 2)[-1].strip()
    source_ts = parts[-1]                # "Infratest-dimap. 24.02.2025, 02:36 Uhr"
    source, _, timestamp = source_ts.partition(". ")
    items = []
    for p in parts[1:-1]:
        m = ITEM_RE.match(p)
        if not m:
            print(f"  unparsed item {p!r} in {chart!r}")
            continue
        cat = f"{m['cat']}: {m['lab']}" if m["lab"] else m["cat"]   # "Union: Einfache Bildung", as in the old nested format
        val, prev = to_float(m["val"]), m["prev"]
        if m["lab2"] and not m["lab2"].startswith("Vgl"):
            # the bracket is a second group ("[Hohe Bildung] 27,0"), not a previous value -> its own row
            items.append({"category": f"{m['cat']}: {m['lab2']}", "value": to_float(prev), "value_prev": None})
            prev = None
        elif prev and prev[0] in "+-±":
            # the bracket is the change since 2021 -> back out the 2021 value. The source writes "+10003,0"
            # for parties without a 2021 value (BSW); that is left empty instead of producing -9999.
            diff = to_float(prev.lstrip("+±"))
            prev = None if abs(diff) > 100 else round(val - diff, 1)
        else:
            prev = to_float(prev)
        items.append({"category": cat, "value": val, "value_prev": prev})
    return chart, items, source, timestamp


def parse_alt_old(alt: str) -> tuple[str, list[dict], str, str]:
    """Split a 2013-2021 alt text ('<title>, in %: cat val, cat val, ..., Quelle: <source>').

    The comparison year ("Vgl. 2017") is kept in the chart title, as in 2025. There is no timestamp and
    no previous value in these alt texts. In the nested variant '<title> (in %) G1: cat val, ...; G2: ...'
    the category becomes "G1: cat".
    """
    m = OLD_SOURCE_RE.search(alt)
    source = m["source"].strip() if m else ""
    body = alt[:m.start()] if m else alt
    if " (in %) " in body:                                   # nested variant
        title, data = body.split(" (in %) ", 1)
        groups = [g.partition(": ")[::2] for g in data.split("; ")]
    else:
        m = OLD_HEAD_RE.match(body)
        if m:
            title, data = m["title"] + m["vgl"], m["data"]
        else:                                                # e.g. "Hochrechnung 23:36 Uhr,  : CDU 18,9; ..."
            title, _, data = body.partition(": ")
        groups = [("", data)]
    items = []
    for group, data in groups:
        for it in OLD_ITEM_RE.finditer(data):
            cat = f"{group}: {it['cat']}" if group else it["cat"]
            items.append({"category": cat, "value": to_float(it["val"]), "value_prev": None})
    return title.strip(" ,"), items, source, ""


def parse_alt(alt: str) -> tuple[str, list[dict], str, str]:
    return parse_alt_2025(alt) if alt.startswith("Umfrage,") else parse_alt_old(alt)


def scrape(election: str, page: str) -> list[dict]:
    html = fetch(BASE.format(election=election, page=page), f"{election}_{page}.html")
    alts = []
    for chart_id, alt in chart_imgs(html, page).items():
        if not is_data(alt):
            # 2021 layout: the numbers are on the chart's own sub-page, which the overview links to with
            # the same id. If there is no such link (a truncated alt text at the source), skip the chart.
            if f"charts/{page}/chart_{chart_id}.shtml" not in html:
                print(f"skip {election}/{page} chart {chart_id}: no data in alt text ({alt[:60]!r})")
                continue
            sub = fetch(SUBPAGE.format(election=election, page=page, chart_id=chart_id),
                        f"{election}_{page}_chart_{chart_id}.html")
            alt = next((a for a in chart_imgs(sub, page).values() if is_data(a)), "")
        if is_data(alt):
            alts.append(alt)
    rows = []
    for alt in dict.fromkeys(alts):      # same chart twice on one page -> once
        chart, items, source, timestamp = parse_alt(alt)
        for it in items:
            rows.append({"election": election, "page": page, "chart": chart, **it,
                         "source": source, "timestamp": timestamp})
    return rows


def main(elections=ELECTIONS, pages=PAGES):
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    for page in pages:
        rows = []
        for election in elections:
            try:
                new = scrape(election, page)
            except requests.HTTPError as e:
                print(f"skip {election}/{page}: {e}")
                continue
            print(f"{election}/{page}: {len(new)} rows")
            rows += new
        out = INTERIM_DIR / f"tagesschau_{page}.csv"
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        print(f"{len(rows)} rows written to {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

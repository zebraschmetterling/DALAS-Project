"""
Scrape the tagesschau.de article archive (headline + teaser per article).

Source : https://www.tagesschau.de/archiv?datum=YYYY-MM-DD[&pageIndex=N]
  * Months older than ~3 months are served as a MONTH view (any day of the
    month redirects to datum=YYYY-MM-01), 140 teasers per page, paginated with
    pageIndex=1,2,...; a page past the end has zero teasers.
  * Recent months are served as a DAY view (one page per day, no pagination).
  The scraper asks for the 1st of the month, reads the <title> to see which
  view it got, and then walks pages or days accordingly.

Raw    : data/raw/tagesschau_archiv/YYYY-MM_pN.html  (month view, one file per page)
         data/raw/tagesschau_archiv/YYYY-MM-DD.html   (day view)
         Existing raw files are reused, so a run can be interrupted and resumed
         and the parser can be re-run offline with --offline.
Interim: data/interim/tagesschau_archiv.csv, one row per teaser:
    datum       YYYY-MM-DD (Europe/Berlin, from data-teaserdate unix timestamp)
    zeit        HH:MM
    ressort     first path segment of the article URL (inland, ausland, wirtschaft, ...)
    label       small badge if present ("Marktbericht", "Interview", "Hintergrund", ...)
    topline     Dachzeile above the headline
    headline
    shorttext   teaser text (1-2 sentences, author credit "Von X." stripped)
    url         absolute article URL (de-duplication key is url + datum + zeit)

Run from the project root:
    python -m src.scraping.tagesschau_archiv                     # 2013-01 .. current month
    python -m src.scraping.tagesschau_archiv --from 2020-03 --to 2020-03   # one month, for testing
    python -m src.scraping.tagesschau_archiv --offline            # re-parse data/raw only
Only needs `requests`; parsing uses the standard library.
"""

import argparse
import csv
import re
import time
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = "https://www.tagesschau.de"
ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "tagesschau_archiv"
OUT = ROOT / "data" / "interim" / "tagesschau_archiv.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (DALAS student project)"}
TZ = ZoneInfo("Europe/Berlin")
SLEEP_S = 1.0          # pause between live requests; be polite
RETRIES = 4            # retries per page on timeout / connection error, with growing back-off
START = (2013, 1)      # AfD founding year; earlier months exist but are sparse

FIELDS = ["datum", "zeit", "ressort", "label", "topline", "headline", "shorttext", "url"]
DAY_TITLE_RE = re.compile(r"Inhalte vom (\d{2})\.(\d{2})\.(\d{4})")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
VOID_TAGS = {"br", "img", "input", "meta", "link", "hr", "source", "wbr"}

# css class on the element -> csv column that collects its text
TEXT_FIELDS = {
    "teaser-right__labeltopline": "topline",
    "teaser-right__headline": "headline",
    "teaser-right__shorttext": "shorttext",
    "label": "label",
}


class TeaserParser(HTMLParser):
    """Collects every div.teaser-right as a dict of raw strings."""

    def __init__(self):
        super().__init__()
        self.teasers = []
        self._stack = []        # one entry per open tag: field name it collects, or None
        self._teaser_depth = None
        self._cur = None

    def handle_starttag(self, tag, attrs):
        if tag in VOID_TAGS:
            if tag == "br" and self._cur is not None:
                self.handle_data(" ")
            return
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        field = None
        if "teaser-right" in classes and self._cur is None:
            self._cur = {f: "" for f in FIELDS}
            self._cur["_ts"] = a.get("data-teaserdate", "")
            self._teaser_depth = len(self._stack)
        elif self._cur is not None:
            if tag == "a" and "teaser-right__link" in classes and not self._cur["url"]:
                self._cur["url"] = a.get("href", "")
            for cls, f in TEXT_FIELDS.items():
                if cls in classes:
                    field = f
        self._stack.append(field)

    def handle_endtag(self, tag):
        if tag in VOID_TAGS or not self._stack:
            return
        self._stack.pop()
        if self._cur is not None and len(self._stack) == self._teaser_depth:
            self.teasers.append(self._cur)
            self._cur = None
            self._teaser_depth = None

    def handle_data(self, data):
        if self._cur is None:
            return
        for field in reversed(self._stack):
            if field:
                self._cur[field] += data
                break


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def tidy(t: dict) -> dict | None:
    """Turn one raw teaser into a csv row; None if it has no timestamp or url."""
    if not t["_ts"] or not t["url"]:
        return None
    when = datetime.fromtimestamp(int(t["_ts"]), TZ)
    url = t["url"] if t["url"].startswith("http") else BASE + t["url"]
    short = clean(t["shorttext"])
    short = re.sub(r"\s*mehr$", "", short)                    # "mehr" link text
    short = re.sub(r"\s*Von [^.]{2,80}\.$", "", short)        # trailing author credit
    path = url.replace(BASE, "").strip("/").split("/")
    return {
        "datum": when.strftime("%Y-%m-%d"),
        "zeit": when.strftime("%H:%M"),
        "ressort": path[0] if path and path[0] else "",
        "label": clean(t["label"]),
        "topline": clean(t["topline"]),
        "headline": clean(t["headline"]),
        "shorttext": short,
        "url": url,
    }


def parse(html: str) -> list[dict]:
    p = TeaserParser()
    p.feed(html)
    return [row for row in (tidy(t) for t in p.teasers) if row]


def is_day_view(html: str) -> bool:
    m = TITLE_RE.search(html)
    return bool(m and DAY_TITLE_RE.search(m.group(1)))


def get(url: str, raw: Path, offline: bool) -> str | None:
    """Return page HTML, from data/raw if already there, else download and store it."""
    if raw.exists():
        return raw.read_text(encoding="utf-8")
    if offline:
        return None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=60)
            r.raise_for_status()
            break
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == RETRIES:
                raise
            print(f"  retry {attempt}/{RETRIES} after network error on {url}: {type(e).__name__}")
            time.sleep(10 * attempt)
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(r.text, encoding="utf-8")
    time.sleep(SLEEP_S)
    return r.text


def fetch_month(year: int, month: int, offline: bool = False) -> list[dict]:
    """All teasers of one month, whichever view the site serves for it."""
    first = f"{year:04d}-{month:02d}-01"
    html = get(f"{BASE}/archiv?datum={first}", RAW_DIR / f"{year:04d}-{month:02d}_p1.html", offline)
    if html is None:
        return []
    if is_day_view(html):
        # recent month: one page per day
        rows = parse(html)
        d = date(year, month, 2)
        while d.month == month and d <= date.today():
            html = get(f"{BASE}/archiv?datum={d:%Y-%m-%d}", RAW_DIR / f"{d:%Y-%m-%d}.html", offline)
            if html is None:
                break
            rows += parse(html)
            d += timedelta(days=1)
        return rows
    # older month: paginated month view
    rows = parse(html)
    page = 2
    while True:                            # walk pages until an empty one
        html = get(f"{BASE}/archiv?datum={first}&pageIndex={page}",
                   RAW_DIR / f"{year:04d}-{month:02d}_p{page}.html", offline)
        if html is None:
            break
        more = parse(html)
        if not more:
            break
        rows += more
        page += 1
    return rows


def months(start: tuple[int, int], end: tuple[int, int]):
    y, m = start
    while (y, m) <= end:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def main(start=START, end=None, offline=False):
    end = end or (date.today().year, date.today().month)
    seen, rows = set(), []
    for y, m in months(start, end):
        got = fetch_month(y, m, offline)
        # de-duplicate on url + timestamp, not url alone: container pages (podcast feed,
        # livestream pages) reuse one url for many distinct items
        key = lambda r: (r["url"], r["datum"], r["zeit"])
        new = [r for r in got if key(r) not in seen]
        seen.update(key(r) for r in new)
        rows += new
        print(f"{y:04d}-{m:02d}: {len(got):5d} teasers, {len(new):5d} new")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows written to {OUT.relative_to(ROOT)}")


def ym(s: str) -> tuple[int, int]:
    y, m = s.split("-")
    return int(y), int(m)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="start", type=ym, default=START, help="first month, YYYY-MM")
    ap.add_argument("--to", dest="end", type=ym, default=None, help="last month, YYYY-MM (default: this month)")
    ap.add_argument("--offline", action="store_true", help="parse data/raw only, no requests")
    args = ap.parse_args()
    main(args.start, args.end, args.offline)

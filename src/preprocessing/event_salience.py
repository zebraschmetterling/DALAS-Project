"""
Weekly salience of an event in the tagesschau.de archive: which share of that
week's teasers mention the event?

Input : data/interim/tagesschau_archiv.csv        (teasers, from src.scraping.tagesschau_archiv)
        data/interim/events_wikipedia_jahr.csv    (events with `stichworte`, from src.scraping.wikipedia_jahr)
Output: printed table per event only. Exploration tool; the dataset is built by
        build_event_salience.py with a different term rule (bursting stems of the
        event text instead of the Wikipedia link titles, see docs/decisions.md 2026-10-13).

Matching rule (first version, deliberately simple so it can be judged):
  * Only teasers from the sections inland, ausland, wirtschaft are used, both for
    matches and for the week total. Other sections (wissen, sport, wetter, ...)
    are not politically relevant and only add noise.
  * A teaser text is topline + headline + shorttext.
  * A teaser matches an event if it contains any of the event's `stichworte`
    (Wikipedia link titles) as a case-insensitive substring. Stichworte shorter
    than MIN_LEN characters are ignored, and a bracketed disambiguation such as
    "Bundespräsident (Schweiz)" is stripped to "Bundespräsident".
  * salience = n_match / n_total for each ISO week from WEEKS_BEFORE before to
    WEEKS_AFTER after the event week. Week 0 is the event's week.
  * Per stichwort the number of matching teasers in the whole window is shown,
    so it is visible which keyword drives the curve (a generic one like "Köln"
    or "Deutschland" will dominate and should be excluded or replaced).

Run from the project root:
    python -m src.preprocessing.event_salience --search Silvesternacht
    python -m src.preprocessing.event_salience --search "Wir schaffen das" --terms "schaffen das|Merkel"
    python -m src.preprocessing.event_salience --search Chemnitz --before 2 --after 8
`--search` picks every event whose text contains the word (case-insensitive);
`--terms` replaces the event's stichworte with your own pipe-separated terms, to test
alternatives. Needs pandas.
"""

import argparse
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
ARCHIV = ROOT / "data" / "interim" / "tagesschau_archiv.csv"
EVENTS = ROOT / "data" / "interim" / "events_wikipedia_jahr.csv"

SECTIONS = ["inland", "ausland", "wirtschaft"]  # politically relevant sections
MIN_LEN = 4                                      # stichworte shorter than this are ignored
WEEKS_BEFORE, WEEKS_AFTER = 4, 12


def load_teasers() -> pd.DataFrame:
    a = pd.read_csv(ARCHIV, usecols=["datum", "ressort", "topline", "headline", "shorttext"])
    a = a[a.ressort.isin(SECTIONS)].copy()
    a["text"] = (a.topline.fillna("") + " " + a.headline.fillna("") + " " + a.shorttext.fillna("")).str.lower()
    a["woche"] = pd.to_datetime(a.datum).dt.to_period("W-SUN")  # ISO weeks (Mon-Sun)
    return a


def clean_terms(stichworte: str) -> list[str]:
    """'Bundespräsident (Schweiz)|Köln|Sexuelle Übergriffe in der Silvesternacht 2015/16' -> usable terms."""
    terms = []
    for t in str(stichworte).split("|"):
        t = re.sub(r"\s*\(.*?\)\s*$", "", t).strip()   # drop bracketed disambiguation
        if len(t) >= MIN_LEN and t.lower() not in terms:
            terms.append(t.lower())
    return terms


def salience_table(teasers: pd.DataFrame, event_date: str, terms: list[str], before: int, after: int) -> tuple[pd.DataFrame, dict]:
    """Weekly n_match / n_total around the event, plus matches per term over the window."""
    week0 = pd.Timestamp(event_date).to_period("W-SUN")
    window = teasers[(teasers.woche >= week0 - before) & (teasers.woche <= week0 + after)]
    per_term = {t: window.text.str.contains(t, regex=False).sum() for t in terms}
    match = pd.Series(False, index=window.index)
    for t in terms:
        match |= window.text.str.contains(t, regex=False)
    g = window.assign(match=match).groupby("woche").agg(n_match=("match", "sum"), n_total=("match", "size"))
    g = g.reindex(pd.period_range(week0 - before, week0 + after, freq="W-SUN"), fill_value=0)
    g["weeks_from_event"] = [(w - week0).n for w in g.index]
    g["salience"] = (g.n_match / g.n_total.replace(0, float("nan"))).round(3)  # empty week -> NaN, not 0
    return g, per_term


def show(event: pd.Series, teasers: pd.DataFrame, terms: list[str], before: int, after: int) -> None:
    g, per_term = salience_table(teasers, event.datum, terms, before, after)
    print("=" * 100)
    print(f"{event.datum}  {event.text[:90]}")
    print("terms:", ", ".join(f"{t} ({n})" for t, n in per_term.items()) or "(none)")
    print(f"{'week':<12}{'w':>4}{'match':>7}{'total':>7}{'salience':>10}  ")
    for w, r in g.iterrows():
        bar = "#" * int((r.salience or 0) * 200)
        print(f"{str(w.start_time.date()):<12}{r.weeks_from_event:>4}{r.n_match:>7}{r.n_total:>7}{r.salience:>10.3f}  {bar}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", required=True, help="pick events whose text contains this (case-insensitive)")
    ap.add_argument("--terms", default=None, help="pipe-separated terms to use instead of the event's stichworte")
    ap.add_argument("--before", type=int, default=WEEKS_BEFORE)
    ap.add_argument("--after", type=int, default=WEEKS_AFTER)
    ap.add_argument("--max", type=int, default=5, help="max events to show")
    args = ap.parse_args()

    teasers = load_teasers()
    ev = pd.read_csv(EVENTS)
    ev = ev[ev.datum.notna() & ev.text.fillna("").str.contains(args.search, case=False, regex=False)]
    if ev.empty:
        raise SystemExit(f"no dated event contains '{args.search}'")
    print(f"{len(ev)} event(s) match '{args.search}', showing {min(len(ev), args.max)}")
    for _, e in ev.head(args.max).iterrows():
        terms = [t.strip().lower() for t in args.terms.split("|")] if args.terms else clean_terms(e.stichworte)
        show(e, teasers, terms, args.before, args.after)


if __name__ == "__main__":
    main()

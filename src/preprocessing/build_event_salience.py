"""
Build the event salience dataset: for every event, how large a share of the
week's political tagesschau.de teasers mention it.

Input : data/interim/tagesschau_archiv.csv        (teasers, from src.scraping.tagesschau_archiv)
        data/interim/events_wikipedia_jahr.csv    (events, from src.scraping.wikipedia_jahr)
Output: data/interim/event_salience_weekly.csv    one row per event and week, -4 .. +12
        data/interim/event_salience_summary.csv   one row per event with the terms used and one score
Both files are candidates for data/processed/ (tables `event_salience` and the
event summary in docs/data_model.md); they stay in interim until the rule is
accepted. Status 2026-10-13: salience only, the tone columns of the data model
are not filled because sentiment exists for one month only.

Method, step by step (constants are imported from burst.py so that there is one
definition; nothing is model-based, nothing leaves the machine):
  1. Teasers: only sections inland, ausland, wirtschaft. Text = topline +
     headline + shorttext, lowercased, cut into stems = words of at least
     MIN_LEN letters, not in STOPWORDS, truncated to STEM_LEN characters.
     Each teaser belongs to its ISO week (Mon-Sun).
  2. Event stems: the same cut applied to the event's own `text`.
  3. Match terms of an event = its stems that *burst* in the event week or the
     week after: present in at least MIN_WEEK_DOCS teasers of that week and
     with share_week / baseline >= BURST_MIN, where baseline is the stem's
     smoothed share over the whole archive (see burst.baseline). Two weeks are
     checked because an event late in the week is covered in the next ISO week.
     Generic stems (deutsch, regier, israel, ...) do not burst and fall out.
  4. Weekly table: for weeks -4 .. +12 around the event week, n_total = political
     teasers in the week, n_match = those containing at least one term,
     salience = n_match / n_total (NaN when the week has no teasers).
  5. Summary per event: peak_share = larger of week 0 and week +1, peak_week
     tells which; baseline_share = mean salience of weeks -4 .. -1;
     salience = peak_share - baseline_share.
Flags (column `flag`, pipe-separated, empty = usable):
  kein_tagesdatum   event has no day-precise date -> no terms, no weekly rows
  ausserhalb_archiv event week 0 or +1 lies outside the archive
  keine_terme       no stem of the event text bursts -> salience NaN
  duenn             fewer than MIN_MATCH matching teasers in the peak week; the
                    value is kept, the analysis decides whether to use it
Known weak points (also in docs/decisions.md): teasers only, three sections only,
the archive is ~10x thinner in 2013 than in 2025, the event sentence from
Wikipedia may not use the words the news used, a term already in the news
(an escalation of an ongoing conflict) does not burst.

Run from the project root (about a minute, pandas only):
    python -m src.preprocessing.build_event_salience
    python -m src.preprocessing.build_event_salience --events data/interim/events_taschenhirn.csv
"""

import argparse
from pathlib import Path

import pandas as pd

from src.preprocessing.burst import MIN_WEEK_DOCS, baseline, load, tokens

ROOT = Path(__file__).resolve().parents[2]
EVENTS = ROOT / "data" / "interim" / "events_wikipedia_jahr.csv"
OUT_WEEKLY = ROOT / "data" / "interim" / "event_salience_weekly.csv"
OUT_SUMMARY = ROOT / "data" / "interim" / "event_salience_summary.csv"

BURST_MIN = 10            # a stem is a match term if its week share is >= this many times its baseline
MIN_MATCH = 3             # fewer matching teasers in the peak week -> flag "duenn"
WEEKS_BEFORE, WEEKS_AFTER = 4, 12
PEAK_WEEKS = [0, 1]       # the peak is looked for in the event week and the one after


def iso_week(p: pd.Period) -> str:
    """Period (W-SUN) -> '2018-W35', the key of polls_weekly."""
    y, w, _ = p.start_time.isocalendar()
    return f"{y}-W{w:02d}"


def find_terms(stems: set[str], week0: pd.Period, week_tok: pd.Series, week_total: pd.Series, base: pd.Series) -> dict[str, float]:
    """Stems of the event text that burst in week0 or week0+1 -> {stem: max burst}."""
    found = {}
    for k in PEAK_WEEKS:
        w = week0 + k
        if w not in week_total.index:
            continue
        for s in stems:
            n = week_tok.get((w, s), 0)
            if n < MIN_WEEK_DOCS or s not in base.index:
                continue
            burst = (n / week_total[w]) / base[s]
            if burst >= BURST_MIN:
                found[s] = max(found.get(s, 0), burst)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", default=str(EVENTS))
    args = ap.parse_args()

    a = load()                                   # political teasers with stem sets, see burst.load
    base = baseline(a)                           # smoothed archive-wide share per stem
    week_total = a.groupby("woche").size()       # teasers per week
    week_tok = a[["woche", "tok"]].explode("tok").groupby(["woche", "tok"]).size()  # teasers per week and stem
    first_week, last_week = a.woche.min(), a.woche.max()

    ev = pd.read_csv(args.events)
    weekly_rows, summary_rows = [], []
    for i, e in ev.iterrows():
        row = dict(event_id=i, quelle=e.quelle, quelle_url=e.quelle_url, datum=e.datum,
                   datum_praezision=e.datum_praezision, text=e.text, terms="", n_terms=0,
                   peak_week=pd.NA, peak_share=float("nan"), n_match_peak=pd.NA, n_total_peak=pd.NA,
                   baseline_share=float("nan"), salience=float("nan"), flag=[])
        if e.datum_praezision != "tag" or pd.isna(e.datum):
            row["flag"].append("kein_tagesdatum")
        else:
            week0 = pd.Timestamp(e.datum).to_period("W-SUN")
            if week0 < first_week or week0 + 1 > last_week:
                row["flag"].append("ausserhalb_archiv")
            terms = find_terms(tokens(str(e.text)), week0, week_tok, week_total, base)
            row["terms"] = "|".join(f"{t}:{b:.0f}" for t, b in sorted(terms.items(), key=lambda x: -x[1]))
            row["n_terms"] = len(terms)
            if not terms:
                row["flag"].append("keine_terme")
            else:
                # weekly table around the event (step 4)
                window = a[(a.woche >= week0 - WEEKS_BEFORE) & (a.woche <= week0 + WEEKS_AFTER)]
                match = window.tok.map(lambda s: not s.isdisjoint(terms))
                g = window.assign(match=match).groupby("woche").agg(n_match=("match", "sum"), n_total=("match", "size"))
                g = g.reindex(pd.period_range(week0 - WEEKS_BEFORE, week0 + WEEKS_AFTER, freq="W-SUN"), fill_value=0)
                g["weeks_from_event"] = [(w - week0).n for w in g.index]
                g["salience"] = g.n_match / g.n_total.replace(0, float("nan"))  # empty week -> NaN
                for w, r in g.iterrows():
                    weekly_rows.append(dict(event_id=i, week=iso_week(w), weeks_from_event=int(r.weeks_from_event),
                                            n_match=int(r.n_match), n_total=int(r.n_total), salience=round(r.salience, 4)))
                # summary (step 5)
                peak = g[g.weeks_from_event.isin(PEAK_WEEKS)]
                p = peak.loc[peak.salience.idxmax()] if peak.salience.notna().any() else None
                if p is not None:
                    row.update(peak_week=int(p.weeks_from_event), peak_share=round(p.salience, 4),
                               n_match_peak=int(p.n_match), n_total_peak=int(p.n_total))
                    row["baseline_share"] = round(g[g.weeks_from_event < 0].salience.mean(), 4)
                    row["salience"] = round(row["peak_share"] - row["baseline_share"], 4)
                    if p.n_match < MIN_MATCH:
                        row["flag"].append("duenn")
        row["flag"] = "|".join(row["flag"])
        summary_rows.append(row)

    weekly = pd.DataFrame(weekly_rows)
    summary = pd.DataFrame(summary_rows)
    weekly.to_csv(OUT_WEEKLY, index=False)
    summary.to_csv(OUT_SUMMARY, index=False)

    usable = summary[summary.flag == ""]
    print(f"events: {len(summary)}  weekly rows: {len(weekly)}")
    print("flags:", summary.flag.replace("", "(ok)").value_counts().to_dict())
    print(f"usable events (no flag): {len(usable)}  median salience {usable.salience.median():.3f}  "
          f"peak in week 0: {(usable.peak_week == 0).sum()}, week +1: {(usable.peak_week == 1).sum()}")
    print("\ntop 10 by salience:")
    print(usable.sort_values("salience", ascending=False).head(10)[["datum", "salience", "terms", "text"]]
          .assign(text=lambda d: d.text.str[:60]).to_string(index=False))


if __name__ == "__main__":
    main()

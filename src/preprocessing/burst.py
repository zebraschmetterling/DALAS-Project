"""
Word bursts in the tagesschau.de archive: which words are used far more in a
given week than usual, and how "important" is a single teaser by that measure?

Input : data/interim/tagesschau_archiv.csv
Output: printed tables (prototype). Later: data/processed/word_bursts.csv and an
        `importance` column per teaser.

Method (all constants at the top, nothing model-based):
  1. Teasers from the sections inland, ausland, wirtschaft only.
  2. Text = topline + headline + shorttext, lowercased. Tokens = words of at least
     MIN_LEN letters that are not in STOPWORDS, cut to STEM_LEN characters so that
     inflections and compounds collapse ("infizierte", "infizierten", "infektion"
     -> "infizi"/"infekt"; crude but transparent).
  3. Baseline share of a token = share of all teasers (whole archive) containing it,
     smoothed: (count + SMOOTH_DOCS) / N. The SMOOTH_DOCS pseudo-documents stop a
     word that occurs only a handful of times in 13 years from getting an absurd
     burst (without it "suisse" scored 190 in March 2023 from 9 teasers).
  4. Burst of a token in a week = share of that week's teasers containing it,
     divided by its smoothed baseline. Tokens must appear in at least MIN_WEEK_DOCS
     teasers of the week to be listed.
  5. Importance of a teaser = mean burst of its TOP_K most bursty tokens in its week
     (raw scale, comparable within a week only). Two normalised versions are added:
     importance_rel = importance / max importance of the week (1 = top story of its week),
     importance_rank = percentile rank within the week (0.9 = in the top 10% of the week).
     Use the normalised ones for anything across weeks or years.

Run from the project root:
    python -m src.preprocessing.burst --week 2020-03-16 --week 2015-09-07
    python -m src.preprocessing.burst --week 2023-03-13 --teasers 8
Needs pandas.
"""

import argparse
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
ARCHIV = ROOT / "data" / "interim" / "tagesschau_archiv.csv"

SECTIONS = ["inland", "ausland", "wirtschaft"]
MIN_LEN = 4          # ignore shorter words
STEM_LEN = 6         # cut words to this many characters
MIN_WEEK_DOCS = 3    # a token needs this many teasers in the week to count as a burst
TOP_K = 3            # importance = mean burst of the teaser's TOP_K burstiest tokens
SMOOTH_DOCS = 20     # pseudo-documents added to every token's archive count (baseline smoothing)
STOPWORDS = set("""
aber alle allem allen aller alles allerdings als also auch auf aus außer bei beide beiden beim bereits besonders
bevor bin bis bisher bislang bleibt dabei dadurch dafür dagegen daher damit danach dann daran darauf daraus darin
darüber darum darunter das dass davon dazu dein deine dem demnach den denen denn dennoch der deren derzeit des
deshalb dessen deswegen die dies diese diesem diesen dieser dieses doch dort dorthin durch eben ebenfalls ehe eher
eigenen eigentlich ein eine einem einen einer eines einige einigen einiger einmal erst erste ersten erstmals etwa
etwas euch euer falls fast für ganz gar geben gegen gegenüber gehen gemacht genau gerade gewesen gibt ging habe
haben hat hatte hatten heraus hier hin hinter ich ihm ihn ihnen ihr ihre ihrem ihren ihrer ihres immer indem
innerhalb insgesamt inzwischen ist jede jedem jeden jeder jedes jedoch jene jetzt kam kann kaum kein keine
keinem keinen keiner keines können könnte könnten kommen kommt künftig lange lassen lässt lediglich machen macht
man manche mehr mehrere mein meine meist meisten mich mir mit mittlerweile muss müssen musste nach nachdem
nämlich neben nicht nichts noch nun nur oben obwohl oder offenbar ohne rund schon sehr sein seine seinem seinen
seiner seines seit seitdem selbst sich sie sind so sogar solche sollen sollte sollten somit sondern sonst sowie
sowohl später statt stets teils trotz über überhaupt übrigen und uns unser unsere unter viel viele vielen vieler
vom von vor vorerst während war waren warum was weder weil weit weiter weitere weiteren weiterhin weitgehend
welche welchem welchen welcher welches wenig weniger wenn wer werde werden wie wieder will wir wird wirklich wo
wohl wollen wollte worden wurde wurden zudem zugleich zum zunächst zur zurück zusammen zuvor zwar zwischen
sagt sagte sagen erklärt erklärte fordert forderte kündigt kündigte teilt teilte heißt berichtet meldet
montag dienstag mittwoch donnerstag freitag samstag sonntag wochenende
januar februar märz april mai juni juli august september oktober november dezember
jahr jahre jahren jahres monat monate monaten woche wochen tage tagen stunden
prozent millionen milliarden euro dollar zahl zahlen mehrheit
deutschland deutsche deutschen deutscher bundesregierung regierung menschen land länder staat
""".split())
TOKEN_RE = re.compile(r"[a-zäöüß]+")


def tokens(text: str) -> set[str]:
    return {w[:STEM_LEN] for w in TOKEN_RE.findall(text.lower()) if len(w) >= MIN_LEN and w not in STOPWORDS}


def load() -> pd.DataFrame:
    a = pd.read_csv(ARCHIV, usecols=["datum", "ressort", "topline", "headline", "shorttext"])
    a = a[a.ressort.isin(SECTIONS)].copy()
    a["text"] = a.topline.fillna("") + " " + a.headline.fillna("") + " " + a.shorttext.fillna("")
    a["woche"] = pd.to_datetime(a.datum).dt.to_period("W-SUN")
    a["tok"] = a.text.map(tokens)
    return a.reset_index(drop=True)


def baseline(a: pd.DataFrame) -> pd.Series:
    """Share of all teasers containing each token (document frequency / N)."""
    df = a.tok.explode().value_counts()
    return (df + SMOOTH_DOCS) / len(a)


def week_bursts(a: pd.DataFrame, base: pd.Series, week: pd.Period) -> pd.DataFrame:
    w = a[a.woche == week]
    counts = w.tok.explode().value_counts()
    counts = counts[counts >= MIN_WEEK_DOCS]
    share = counts / len(w)
    out = pd.DataFrame({"n_week": counts, "share_week": share, "baseline": base.reindex(counts.index)})
    out["burst"] = out.share_week / out.baseline
    return out.sort_values("burst", ascending=False)


def importance(a: pd.DataFrame, bursts: pd.DataFrame, week: pd.Period) -> pd.DataFrame:
    w = a[a.woche == week].copy()
    b = bursts.burst

    def score(toks):
        vals = sorted((b.get(t, 1.0) for t in toks), reverse=True)[:TOP_K]
        return sum(vals) / len(vals) if vals else 1.0

    w["importance"] = w.tok.map(score).round(1)
    w["importance_rel"] = (w.importance / w.importance.max()).round(2)   # 1 = top story of the week
    w["importance_rank"] = w.importance.rank(pct=True).round(2)          # percentile within the week
    return w.sort_values("importance", ascending=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", action="append", required=True, help="any date inside the week, YYYY-MM-DD")
    ap.add_argument("--words", type=int, default=15, help="bursting tokens to list")
    ap.add_argument("--teasers", type=int, default=5, help="top/bottom teasers to list")
    args = ap.parse_args()

    a = load()
    base = baseline(a)
    for d in args.week:
        week = pd.Timestamp(d).to_period("W-SUN")
        bursts = week_bursts(a, base, week)
        n = (a.woche == week).sum()
        print("=" * 100)
        print(f"week of {week.start_time.date()}  ({n} teasers)")
        print(f"{'token':<12}{'n_week':>7}{'share':>8}{'baseline':>10}{'burst':>8}")
        for t, r in bursts.head(args.words).iterrows():
            print(f"{t:<12}{r.n_week:>7}{r.share_week:>8.2f}{r.baseline:>10.4f}{r.burst:>8.1f}")
        imp = importance(a, bursts, week)
        print(f"\n  most important teasers:")
        print(f"  {'raw':>6} {'rel':>5} {'rank':>5}")
        for _, r in imp.head(args.teasers).iterrows():
            print(f"  {r.importance:>6.1f} {r.importance_rel:>5.2f} {r.importance_rank:>5.2f}  {r.text[:95]}")
        print(f"  least important teasers:")
        for _, r in imp.tail(args.teasers).iterrows():
            print(f"  {r.importance:>6.1f} {r.importance_rel:>5.2f} {r.importance_rank:>5.2f}  {r.text[:95]}")


if __name__ == "__main__":
    main()

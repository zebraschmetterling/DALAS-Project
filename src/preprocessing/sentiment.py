"""
Score the tone of tagesschau.de teasers with a German sentiment model.

Model : oliverguhr/german-sentiment-bert, called directly through `transformers`
        (BERT fine-tuned on German reviews, tweets and news-like text;
        classes positive / negative / neutral). Downloads ~450 MB on first run.
        The `germansentiment` wrapper package is not used: it is incompatible
        with current transformers releases.
Input : data/interim/tagesschau_archiv.csv (from src.scraping.tagesschau_archiv)
Output: data/interim/tagesschau_sentiment.csv, one row per teaser in the chosen range:
    url, datum, zeit, ressort, flag, text, sentiment, p_pos, p_neg, p_neu, tone
    flag       "" for normal news; "wetter" / "markt" / "live" / "podcast" for
               routine items that should be excluded from tone aggregates
    sentiment  argmax class
    tone       p_pos - p_neg, in [-1, 1]; the number to average per week / event

The text scored is topline + headline + shorttext. Headlines alone are too short
and often ironic; the teaser sentence carries the actual framing.

Run from the project root:
    python -m src.preprocessing.sentiment --from 2026-03 --to 2026-03   # one month, to inspect
    python -m src.preprocessing.sentiment                                # everything (slow on CPU: ~60k rows)
Needs: pandas, torch, transformers.
"""

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "data" / "interim" / "tagesschau_archiv.csv"
OUT = ROOT / "data" / "interim" / "tagesschau_sentiment.csv"
MODEL = "oliverguhr/german-sentiment-bert"
BATCH = 64
MAX_TOKENS = 128      # teasers are short; longer inputs are truncated

FLAG_RULES = {            # ressort or url substring -> flag
    "wetter": "wetter",
    "marktbericht": "markt",
    "boersenbericht": "markt",
    "livestream": "live",
    "podcast": "podcast",
}


def flag(row) -> str:
    hay = f"{row.ressort} {row.url} {row.label}".lower()
    for needle, f in FLAG_RULES.items():
        if needle in hay:
            return f
    return ""


def load(start: str | None, end: str | None) -> pd.DataFrame:
    df = pd.read_csv(SRC, dtype=str).fillna("")
    month = df["datum"].str[:7]
    keep = pd.Series(True, index=df.index)
    if start:
        keep &= month >= start
    if end:
        keep &= month <= end
    df = df[keep].copy()
    df["flag"] = df.apply(flag, axis=1)
    df["text"] = (df["topline"] + ". " + df["headline"] + ". " + df["shorttext"]).str.replace(r"^\. ", "", regex=True)
    return df


def score(texts: list[str]) -> pd.DataFrame:
    """Run the BERT classifier over all texts in batches; returns one row per text."""
    import torch  # imported here so --help works without torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL).to(device).eval()
    labels = [model.config.id2label[i] for i in range(model.config.num_labels)]  # positive, negative, neutral
    rows = []
    with torch.no_grad():
        for i in range(0, len(texts), BATCH):
            batch = texts[i:i + BATCH]
            enc = tok(batch, padding=True, truncation=True, max_length=MAX_TOKENS, return_tensors="pt").to(device)
            probs = torch.softmax(model(**enc).logits, dim=-1).cpu().numpy()
            for p in probs:
                d = dict(zip(labels, p.tolist()))
                rows.append({"sentiment": max(d, key=d.get), "p_pos": d["positive"], "p_neg": d["negative"], "p_neu": d["neutral"]})
            print(f"  scored {min(i + BATCH, len(texts))}/{len(texts)}", end="\r")
    print()
    out = pd.DataFrame(rows)
    out["tone"] = out["p_pos"] - out["p_neg"]
    return out


def summary(df: pd.DataFrame) -> None:
    news = df[df["flag"] == ""]
    print(f"\n{len(df)} teasers scored, {len(news)} after dropping flagged items")
    print("\nclass distribution (news only):")
    print(news["sentiment"].value_counts(normalize=True).round(3).to_string())
    print("\nmean tone by ressort (news only, n >= 20):")
    g = news.groupby("ressort")["tone"].agg(["mean", "count"])
    print(g[g["count"] >= 20].sort_values("mean").round(3).to_string())
    print("\nmean tone by week (news only):")
    wk = news.assign(week=pd.to_datetime(news["datum"]).dt.to_period("W").astype(str))
    print(wk.groupby("week")["tone"].agg(["mean", "count"]).round(3).to_string())
    for name, sub in (("most negative", news.nsmallest(5, "tone")), ("most positive", news.nlargest(5, "tone"))):
        print(f"\n{name}:")
        for _, r in sub.iterrows():
            print(f"  {r.tone:+.2f}  {r.datum}  {r.headline}")


def main(start=None, end=None):
    df = load(start, end)
    if df.empty:
        raise SystemExit(f"no rows in {SRC.relative_to(ROOT)} for {start}..{end}")
    print(f"{len(df)} teasers from {df.datum.min()} to {df.datum.max()}")
    scored = score(df["text"].tolist())
    df = pd.concat([df.reset_index(drop=True), scored], axis=1)
    cols = ["url", "datum", "zeit", "ressort", "flag", "text", "sentiment", "p_pos", "p_neg", "p_neu", "tone"]
    df[cols].round(4).to_csv(OUT, index=False)
    print(f"written to {OUT.relative_to(ROOT)}")
    summary(df)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Sentiment / tone of tagesschau teasers")
    ap.add_argument("--from", dest="start", default=None, help="first month YYYY-MM")
    ap.add_argument("--to", dest="end", default=None, help="last month YYYY-MM")
    a = ap.parse_args()
    main(a.start, a.end)

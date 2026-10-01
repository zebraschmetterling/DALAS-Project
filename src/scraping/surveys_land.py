"""
Scrapes state-level polls for the federal election ("Sonntagsfrage in den
Ländern") from wahlrecht.de.

What this script does:
- Reads the single overview page, which contains one table section per
  Bundesland.
- Splits the combined cells into "Institut" / "Datum" and "Befragte" /
  "Zeitraum", and adds a "Bundesland" column.
- Moves FW and BSW values out of "Sonstige" into their own columns (as in
  surveys_bund).
- Skips the rows showing actual election results ("Bundestagswahl am ...").
- Takes all surveys back to the point where the AfD was founded
  (6 February 2013); older polls are discarded.
- Saves everything as "surveys_land.csv" in data/interim.
"""
import re
from pathlib import Path
from urllib.request import Request, urlopen

import bs4
import pandas as pd

URL = "https://www.wahlrecht.de/umfragen/laender.htm"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "interim"

DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")
MARKER_RE = re.compile(r"^(?:TOM|T|O|F)\s*•\s*")                  # survey method, e.g. "T •"
BEFRAGTE_RE = re.compile(r"^(≈?\d{1,3}(?:\.\d{3})*|\?)(?=\s|$)\s*(.*)$")


def fetch_html(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return bs4.BeautifulSoup(urlopen(req).read(), "lxml")


def clean(cell):
    return " ".join(cell.get_text(" ", strip=True).split())


def split_befragte_zeitraum(text):
    text = MARKER_RE.sub("", text)
    m = BEFRAGTE_RE.match(text)
    return (m.group(1), m.group(2)) if m else ("", text)


def split_sonstige(text):
    """'FW 3 %  PIRATEN 2 %  Sonst. 5 %' -> FW='3 %', Sonstige='7 %'"""
    out = {}
    for key in ("FW", "BSW"):
        m = re.search(rf"\b{key}\s+(\d+(?:,\d+)?\s*%|\?|–)", text)
        if m:
            out[key] = m.group(1)
            text = text.replace(m.group(0), " ")

    # sum all remaining parties (PIRATEN, REP/NPD, Rechte, Sonst., ...)
    numbers = [float(n.replace(",", ".")) for n in re.findall(r"(\d+(?:,\d+)?)\s*%", text)]
    if "?" in text or not numbers:                 # unknown or nothing reported
        out["Sonstige"] = pd.NA
    else:
        total = round(sum(numbers), 1)
        total = int(total) if total == int(total) else str(total).replace(".", ",")
        out["Sonstige"] = f"{total} %"
    return out


def extract_all(cutoff="2013-02-06", out="surveys_land.csv"):
    cutoff = pd.Timestamp(cutoff)
    page = fetch_html(URL)

    rows, land, parties = [], None, []
    for tr in page.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        first = clean(cells[0])

        # section header: "Baden-Württemberg (Bundestagswahl)"
        if first.endswith("(Bundestagswahl)"):
            land = first.replace("(Bundestagswahl)", "").strip()
            continue

        # column header of a section: party names = non-empty cells after "Befragte"
        if first.startswith("Institut") and len(cells) > 4:
            texts = [clean(c) for c in cells]
            idx = next((i for i, t in enumerate(texts) if t.startswith("Befragte")), 2)
            parties = ["CDU/CSU" if t in ("CDU", "CSU") else t
                       for t in texts[idx + 1:] if t]
            continue

        # data rows only
        n = len(parties)
        if land is None or not n or len(cells) < n + 2:
            continue
        if "bundestagswahl" in first.lower():          # actual election result
            continue
        m = DATE_RE.search(first)
        if not m:
            continue

        # party values = the LAST n cells, whatever comes before them
        values = cells[-n:]
        middle = cells[1:-n]                            # Auftraggeber, Befragte/Zeitraum, spacer
        bz_text = " ".join(t for t in (clean(c) for c in middle[1:]) if t)
        befragte, zeitraum = split_befragte_zeitraum(bz_text)

        row = {
            "Bundesland": land,
            "Institut": first[:m.start()].strip(" ("),
            "Datum": m.group(0),
            "Befragte": befragte,
            "Zeitraum": zeitraum,
        }
        for name, cell in zip(parties, values):
            row[name] = clean(cell)

        if "Sonstige" in row:
            row.update(split_sonstige(row["Sonstige"]))
        rows.append(row)

    df = pd.DataFrame(rows)
    print("Rows per Bundesland before cutoff:\n", df["Bundesland"].value_counts().to_string())

    # cutoff
    dates = pd.to_datetime(df["Datum"], format="%d.%m.%Y", errors="coerce")
    df = df[dates >= cutoff]

    # same layout as the federal file: ..., AfD, FW, BSW, Sonstige, Befragte, Zeitraum
    for col in ("FW", "BSW"):
        if col not in df.columns:
            df[col] = pd.NA
    head = ["Bundesland", "Institut", "Datum"]
    tail = ["FW", "BSW", "Sonstige", "Befragte", "Zeitraum"]
    middle_cols = [c for c in df.columns if c not in head + tail]
    df = df[head + middle_cols + tail]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_DIR / out, index=False)
    print(f"Saved {len(df)} rows to {OUTPUT_DIR / out}")
    return df

df = extract_all()
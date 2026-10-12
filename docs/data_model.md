# Data model (processed layer)

Target tables in `data/processed/`, built by `src/preprocessing/` from the per-source CSVs in `data/interim/`.
Each table keeps its natural grain. Joins happen inside the analysis, not in storage.

Research question: *How can the growth of the AfD be explained?*
- Q1: Which international and national events had a significant impact on the AfD's success?
- Q2: How has the demographic profile of AfD voters changed?

## Diagram

```mermaid
erDiagram
    events ||--o{ event_salience : "one row per week"
    events }o..o{ afd_weekly : "joined by date window (analysis only)"

    afd_weekly {
        string week PK "ISO week, 2013-W05 .. now"
        float afd_raw "mean of all polls in the week"
        float afd_adj "house-effect adjusted mean"
        int n_polls
        float gov_satisfied "zufrieden + sehr zufrieden, monthly, carried forward"
        int asylum_applications "BAMF, monthly, carried forward (to collect)"
        float unemployment_rate "BA, monthly, carried forward (to collect)"
    }

    events {
        int event_id PK
        date date "day the event happened or became public"
        string title
        string type "international | national"
        string keywords "pipe-separated match terms for tagesschau teasers"
        string date_source "URL the date was taken from"
        string text_source "taschenhirn | added"
    }

    event_salience {
        int event_id FK
        string week "ISO week"
        int weeks_from_event "-4 .. +12"
        int n_match "teasers matching keywords"
        int n_total "all teasers that week"
        float salience "n_match / n_total"
        float tone "mean sentiment of matching teasers"
        float tone_background "mean sentiment of all teasers that week"
    }

    afd_by_group {
        date election_date PK
        string level PK "Bund | Land"
        string bundesland PK "empty for Bund"
        string group_type PK "age | gender | education | occupation"
        string group_label PK "as printed in the source"
        float afd_share "percent"
        string source "script that produced the row"
    }

    wahlkreise {
        int year PK
        string wahlkreis PK
        float afd_zweitstimmen_share
        float turnout
        string east_west
        float structural_vars "Bundeswahlleiterin Strukturdaten (to collect)"
    }
```

## Tables

| Table | Grain | Built from | Serves |
|---|---|---|---|
| `afd_weekly` | one row per ISO week | `surveys_bund.csv`, `regierungszufriedenheit.csv`, controls to collect | Q1 outcome and controls |
| `events` | one row per event | `events_taschenhirn.csv` plus dates and keywords added by `src/preprocessing/events.py` | Q1 treatments |
| `event_salience` | one row per event and week | `events`, `tagesschau_archiv.csv`, `tagesschau_sentiment.csv` | Q1 exposure |
| `afd_by_group` | one row per election, level, group type, group | `btw_gender_age.csv`, `btw_education.csv`, `ltw_age.csv`, `ltw_gender.csv`, `ltw_education.csv`, `tagesschau_umfrage-*.csv` | Q2 descriptive trend |
| `wahlkreise` | one row per Wahlkreis and election | `btw_wahlkreise.csv`, structural data to collect | Q2 regression and map |

## Notes

- **House effects** (`afd_adj`): each institute's mean deviation from the all-institute mean over the whole period is subtracted before averaging by week. The offsets are written to the decisions log.
- **Monthly controls** are carried forward to the weeks of the month they cover. This is stated wherever they are used.
- **Salience** is a share within the week, never a count, because the archive grows roughly tenfold between 2013 and 2025. `n_match` and `n_total` are kept so small denominators are visible.
- **Event summaries** (peak salience, salience summed over a window) are derived from `event_salience` at the end of the pipeline, not stored on `events`, so there is one definition.
- **`afd_by_group`** replaces the five wide interim tables with one long table. Group labels are kept as printed; harmonisation (e.g. 16-29 vs 18-29) is an analysis decision.
- **Mixed grains** are intended: Q1 is answered at weekly grain, Q2 at election grain. The two meet only in the dashboard.

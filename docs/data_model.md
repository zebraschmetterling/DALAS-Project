# Data model (processed layer)

Target tables in `data/processed/`, built by `src/preprocessing/` from the per-source CSVs in `data/interim/`.
Each table keeps its natural grain. Joins happen inside the analysis, not in storage.
All party tables carry every party, not only the AfD, so shifts between parties (e.g. AfD to BSW in 2024) stay visible.

Research question: *How can the growth of the AfD be explained?*
- Q1: Which international and national events had a significant impact on the AfD's success?
- Q2: How has the demographic profile of AfD voters changed?

Standard party columns everywhere: `CDU/CSU, SPD, Grüne, FDP, Linke, AfD, BSW, FW, Sonstige` (plus `SSW` and others where they occur).

## Diagram

```mermaid
erDiagram
    events ||--o{ event_salience : "one row per week"
    events }o..o{ polls_weekly : "joined by date window (analysis only)"
    election_results }o..o{ polls_weekly : "ground truth for polls (analysis only)"
    election_results }o..|| wahlkreise : "aggregates of"

    polls_weekly {
        string week PK "ISO week, 2013-W05 .. now"
        int n_polls
        float party_raw "one column per party: mean of all polls in the week"
        float party_adj "one column per party: house-effect adjusted mean"
        float gov_satisfied "zufrieden + sehr zufrieden, monthly, carried forward"
        int asylum_applications "BAMF, monthly, carried forward (to collect)"
        float unemployment_rate "BA, monthly, carried forward (to collect)"
    }

    events {
        int event_id PK
        date date "day the event happened or became public"
        string title
        string type "international | national"
        string keywords "pipe-separated match terms we define per event"
        string source "taschenhirn | added"
        string date_source "URL the date was taken from"
    }

    event_salience {
        int event_id FK
        string week "ISO week; each week appears once per event"
        int weeks_from_event "week minus event week, -4 .. +12"
        int n_match "teasers matching keywords in that week"
        int n_total "all teasers in that week"
        float salience "n_match / n_total"
        float tone "mean sentiment of matching teasers"
        float tone_background "mean sentiment of all teasers that week"
    }

    votes_by_group {
        date election_date PK
        string level PK "Bund | Land"
        string bundesland PK "empty for Bund"
        string group_type PK "age | gender | education | occupation"
        string group_label PK "as printed in the source"
        string party PK
        float share "percent"
        string source "script that produced the row"
    }

    election_results {
        date election_date PK
        string level PK "Bund | Land"
        string gebiet PK "Bundesgebiet, Bundesland"
        string party PK
        float share "percent of second votes / decisive votes"
    }

    wahlkreise {
        int year PK
        string wahlkreis PK
        string bundesland
        string east_west
        float turnout
        float party_share "one column per party, second votes"
        float structural_vars "Bundeswahlleiterin Strukturdaten (to collect)"
    }
```

## Tables

| Table | Grain | Built from | Serves |
|---|---|---|---|
| `polls_weekly` | one row per ISO week | `surveys_bund.csv`, `regierungszufriedenheit.csv`, controls to collect | Q1 outcome (all parties) and controls |
| `events` | one row per event | `events_taschenhirn.csv` plus dates and keywords added by `src/preprocessing/events.py` | Q1 treatments |
| `event_salience` | one row per event and week | `events`, `tagesschau_archiv.csv`, `tagesschau_sentiment.csv` | Q1 exposure |
| `votes_by_group` | one row per election, level, group type, group, party | `btw_gender_age.csv`, `btw_education.csv`, `ltw_age.csv`, `ltw_gender.csv`, `ltw_education.csv`, `tagesschau_umfrage-*.csv` | Q2 descriptive trend, flows between parties within a group |
| `election_results` | one row per election, area, party | `btw_results.csv`, `ltw_results.csv` | ground truth vs polls; Q1 and Q2 context |
| `wahlkreise` | one row per Wahlkreis and election | `btw_wahlkreise.csv`, structural data to collect | Q2 regression and map |

## Notes

- **House effects** (`party_adj`): each institute's mean deviation from the all-institute mean, per party, over the whole period is subtracted before averaging by week. The offsets are written to the decisions log.
- **Monthly controls** are carried forward to the weeks of the month they cover. This is stated wherever they are used.
- **Salience** is a share within the week, never a count, because the archive grows roughly tenfold between 2013 and 2025. `n_match` and `n_total` are kept so small denominators are visible. One row is one calendar week; `weeks_from_event` only aligns events on a common axis (week 0 = event week) so their coverage curves can be compared.
- **Event summaries** (peak salience, salience summed over a window) are derived from `event_salience` at the end of the pipeline, not stored on `events`, so there is one definition.
- **Keywords** are written by us per event and documented in `events`; they are not taken from a source.
- **`votes_by_group`** replaces the five wide interim tables with one long table with a `party` column. Group labels are kept as printed; harmonisation (e.g. 16-29 vs 18-29) is an analysis decision.
- **`wahlkreise`** gives 299 observations per federal election instead of 16 states, which is what makes a regression of party share on constituency characteristics possible, and drives the map in the dashboard.
- **Mixed grains** are intended: Q1 is answered at weekly grain, Q2 at election grain. The two meet only in the dashboard.

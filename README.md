# DALAS project: geopolitical events and Germany

Course project for DALAS (Sorbonne, exchange semester 2026/27).
Question and dataset are still preliminary; see `docs/` for the running log.

## Layout
- `data/raw/`        untouched downloads (HTML snapshots, API JSON). Never edited by hand.
- `data/interim/`    one cleaned CSV per source; `not_used/` holds files of sources that were evaluated and dropped.
- `data/processed/`  the merged dataset the analysis and dashboard use.
- `src/scraping/`    one module per source (`taschenhirn.py`, ...).
- `src/preprocessing/` cleaning, date parsing, merging, cut-offs.
- `src/analysis/`    one file per method from the course.
- `notebooks/`       exploration only; nothing in the report depends on a notebook.
- `dashboard/`       Dash app for deliverable 2.
- `reports/`         deliverable 1 (two-pager), deliverable 3 (LaTeX report), technical report.
- `docs/sources.md`  every data source with URL, scrape date, row count.
- `docs/decisions.md` every preprocessing / modelling choice and why.
- `docs/data_model.md` target tables of the processed layer (Mermaid ER diagram).
- `docs/project_brief.md` the teacher's project description and deliverable requirements.

## Setup
Uses the course venv in `../.venv`:
```bash
cd "<path>/DALAS/project"
source ../.venv/bin/activate
pip install -r requirements.txt
```

## Run
All scrapers cache their raw download in `data/raw/` and skip the request if the file exists (`--force` where supported).
```bash
# events (all write the same column layout, see docs/data_model.md)
python -m src.scraping.wikipedia_jahr          # -> data/interim/events_wikipedia_jahr.csv   (main event source)
python -m src.scraping.taschenhirn             # -> data/interim/events_taschenhirn.csv
python -m src.scraping.geopol_timeline         # -> data/interim/events_geopol.csv
python -m src.scraping.wikipedia_crises_en     # -> data/interim/events_wikipedia_crises_en.csv
# tagesschau archive + exit polls
python -m src.scraping.tagesschau_archiv --from 2020-03 --to 2020-03   # test one month -> data/interim/tagesschau_archiv.csv
python -m src.scraping.tagesschau_archiv                               # full run 2013-01 .. now (~1 request/s, 15-30 min)
python -m src.scraping.tagesschau_umfrage      # -> data/interim/tagesschau_umfrage-{alter,werwas,afd}.csv
# event salience (needs the archive and the Wikipedia events)
python -m src.preprocessing.build_event_salience  # -> data/interim/event_salience_{weekly,summary}.csv
# elections, polls, structure, controls
python -m src.scraping.btw_strukturdaten       # -> data/interim/btw_strukturdaten.csv
python -m src.scraping.controls_monthly        # -> data/interim/controls_monthly.csv
# plus the election and poll scrapers: surveys_bund, surveys_land, ltw_surveys, btw_results, btw_wahlkreise,
# btw_gender_age, btw_education, ltw_results, ltw_age, ltw_gender, ltw_education, regierungszufriedenheit
```

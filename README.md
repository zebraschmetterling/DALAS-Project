# DALAS project: geopolitical events and Germany

Course project for DALAS (Sorbonne, exchange semester 2026/27).
Question and dataset are still preliminary; see `docs/` for the running log.

## Layout
- `data/raw/`        untouched downloads (HTML snapshots, API JSON). Never edited by hand.
- `data/interim/`    one cleaned CSV per source.
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

## Setup
Uses the course venv in `../.venv`:
```bash
cd "<path>/DALAS/project"
source ../.venv/bin/activate
pip install -r requirements.txt
```

## Run
```bash
python -m src.scraping.taschenhirn     # -> data/interim/events_taschenhirn.csv
python -m src.scraping.tagesschau_archiv --from 2020-03 --to 2020-03   # test one month -> data/interim/tagesschau_archiv.csv
python -m src.scraping.tagesschau_archiv                               # full run 2013-01 .. now (~1 request/s, 15-30 min)
```

# Project rules

Course project for DALAS. Two students, graded on code, a dashboard, and a written report.
Read `README.md` for the layout, `docs/data_model.md` for the target tables, `docs/decisions.md` for choices already made.

## Code
- Keep code as simple as possible. Prefer plain pandas and the standard library over clever abstractions.
- Every script gets a short module docstring: what it reads, what it writes, how to run it.
- Comment the non-obvious steps so either of us can explain any line.
- One scraper per source in `src/scraping/`, writing one CSV to `data/interim/`. Raw downloads go to `data/raw/` and are never edited by hand.
- Downloads are cached: skip the request if the raw file already exists.
- Use the standard party column names from `docs/data_model.md`.
- Dates as ISO strings (`YYYY-MM-DD`), numbers as plain floats, no percent signs or thousands separators in interim CSVs.

## Transparency
- Every preprocessing or modelling choice goes into `docs/decisions.md` under today's date, with the reason.
- Every new source goes into `docs/sources.md` with URL, scrape date, row count, script.
- No hand-entered data. Every value in `data/` is scraped or parsed from a source; event rows carry `quelle`, `quelle_url`, `quelle_permalink`, `abgerufen_am`, the raw text and the raw date fragment. If a value cannot be parsed, leave it empty and flag it, never fill it in from memory.
- When something is skipped, approximated, or left NaN, say so in the docstring and in the decisions log.
- Do not silently fix data. If a source has an error, keep the source value and document the correction as a separate step.

## Git
- Commit messages describe what changed and why in one line.
- Do not commit `data/raw/`.

# Data sources

One entry per source. Fill in when a scraper first runs.

| Source | URL | Scraped on | Rows | Script                        | Notes |
|---|---|---|------|-------------------------------|---|
| taschenhirn.de, 21st-century chronicle | https://www.taschenhirn.de/geschichte/aktuelle-geschichte-des-21-jahrhunderts/ | |      | `src/scraping/taschenhirn.py` | one table, year + event text; multi-event cells split per paragraph |
| wahlrecht.de, Sonntagsfrage polls of 8 institutes | https://www.wahlrecht.de/umfragen/ | 2026-09-30 | 3301 | `src/scraping/surveys_bund.py`    | polls from several institutes, each with its historical survey series |
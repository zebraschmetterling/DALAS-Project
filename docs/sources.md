# Data sources

One entry per source. Fill in when a scraper first runs.

| Source | URL | Scraped on | Rows | Script                        | Notes                                                                                              |
|---|---|---|------|-------------------------------|----------------------------------------------------------------------------------------------------|
| taschenhirn.de, 21st-century chronicle | https://www.taschenhirn.de/geschichte/aktuelle-geschichte-des-21-jahrhunderts/ | |      | `src/scraping/taschenhirn.py` | one table, year + event text; multi-event cells split per paragraph                                |
| wahlrecht.de, Sonntagsfrage polls of 8 institutes | https://www.wahlrecht.de/umfragen/ | 2026-09-30 | 3301 | `src/scraping/surveys_bund.py`    | polls from several institutes, each with its historical survey series                              |
| wahlrecht.de, Sonntagsfrage polls by Bundesland | https://www.wahlrecht.de/umfragen/laender.htm | 2026-10-01 | 275 | `src/scraping/surveys_land.py` | state-level polls for the Bundestag election from several institutes, all Bundesländer on one page |
| Bundeswahlleiterin, Bundestagswahlen Wahlkreiseergebnisse | https://www.bundeswahlleiterin.de/bundestagswahlen/2025/ergebnisse.html | 2026-10-06 | 1196 | `src/scraping/btw_wahlkreise.py` | Final constituency-level federal election results (2013–present)|
| Bundeswahlleiterin, Repräsentative Wahlstatistik, Zweitstimmen nach Geschlecht und Altersgruppen (Zeitreihe seit 1953) | https://www.bundeswahlleiterin.de/bundestagswahlen/2025/ergebnisse/repraesentative-wahlstatistik.html | 2026-10-07 | 96 | `src/scraping/btw_gender_age.py` | representative election statistics: votes by gender and age group |
| ARD DeutschlandTrend, satisfaction with the federal government | https://www.tagesschau.de/inland/deutschlandtrend | 2026-10-07 | 133 | `src/scraping/regierungszufriedenheit.py` | monthly trend survey: satisfaction with the federal government, February 2013 onward |
| Forschungsgruppe Wahlen, Kurzanalysen Bundestagswahl, Wahlentscheidung in den Bildungsgruppen | https://www.forschungsgruppe.de/Wahlen/Wahlanalysen/ | 2026-10-08 | 12 | `src/scraping/btw_education.py` | exit-poll vote shares by education group from the post-election reports (2013–present) |

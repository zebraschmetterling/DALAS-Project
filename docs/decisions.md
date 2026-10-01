# Decisions log

Every preprocessing or modelling choice, with the reason, so it can be defended in the weekly discussions and the technical report.

## 2026-09-30
- Project layout: raw / interim / processed data split so every step can be rerun from the untouched downloads.
- taschenhirn.py: cells with several events are split at paragraph and line breaks into one row per event. Year ranges (e.g. "2010-2012") are kept as text plus `jahr_start` / `jahr_ende` integers.
- Time window: source covers 2000 onward; a cut to 2013 (AfD founding) is a preprocessing option, not applied at scrape time.
- surveys_bund.py: polls are scraped for all institutes listed in the overview table, from the current page back through the yearly archive pages, and concatenated with an added `Institut` column, so columns missing for some institutes or years (e.g. BSW, FW) stay NaN. Only unnamed (spacer) columns are dropped, cell contents stay untouched, and "Bundestagswahl" entries (actual election results, not polls) are excluded. Polls are cut at 2013-02-06 (AfD founding) already at scrape time; changing the window means rerunning the scraper with another `cutoff`.

## 2026-10-01
- surveys_land.py: all Bundesland sections are read from the single overview page and concatenated with an added `Bundesland` column, in the same layout as surveys_bund. The combined cells "Institut (Datum)" and "Befragte / Zeitraum" are split into separate columns, `CDU` and `CSU` are merged into `CDU/CSU`, and the client (`Auftraggeber`) is dropped. `FW` and `BSW` are split out of `Sonstige` into their own columns; all other remaining parties (e.g. PIRATEN) are summed into a single `Sonstige` value, which is left empty if any part is unknown. Party values are read from the right-hand end of each row, because the number of leading cells varies between sections. "Bundestagswahl" rows (actual election results, not polls) are excluded, and, as in surveys_bund, polls are cut at 2013-02-06 (AfD founding) at scrape time.
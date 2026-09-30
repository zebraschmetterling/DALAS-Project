# Decisions log

Every preprocessing or modelling choice, with the reason, so it can be defended in the weekly discussions and the technical report.

## 2026-09-30
- Project layout: raw / interim / processed data split so every step can be rerun from the untouched downloads.
- taschenhirn.py: cells with several events are split at paragraph and line breaks into one row per event. Year ranges (e.g. "2010-2012") are kept as text plus `jahr_start` / `jahr_ende` integers.
- Time window: source covers 2000 onward; a cut to 2013 (AfD founding) is a preprocessing option, not applied at scrape time.

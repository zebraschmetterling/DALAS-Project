# How can the growth of the AfD be explained?

**Deliverable 1: research question and dataset**
Jan Rothweiler, Rebecca Prechtl — DALAS, Sorbonne Université, 2026/27
2026-10-23 — Repository: https://github.com/zebraschmetterling/DALAS-Project

*Same content as `report.tex`; this copy is for editing together in Notion. Numbers read off `data/interim/` on 2026-10-23.*

## Research question

Since its founding in February 2013 the AfD has gone from a party below the 5 % threshold to the second-largest party in the 2025 federal election. We ask what explains this growth, split into two questions that use different parts of the dataset.

- **Q1 (events).** Which international and national events had a measurable effect on the AfD's polling, and how does that effect compare across parties?
- **Q2 (voters).** How has the profile of AfD voters changed over the four federal elections 2013–2025 and the state elections since 2013, by age, gender and education, and which constituency characteristics go with a high AfD share?

Both are questions about the real world, not about the dataset. They may be sharpened during the semester; the overall objective, explaining AfD growth from events and voter structure, stays.

## Data collection

The dataset is built entirely by us from public sources with 20 scraper scripts (`src/scraping/`), one per source. Every raw download (about 1,000 HTML, CSV, ZIP, JSON and PDF files) is cached in `data/raw/` and never edited, so every step can be rerun offline; each script writes one cleaned CSV to `data/interim/`. No value is typed in by hand. Event rows carry the source URL, the Wikipedia revision permalink, the fetch time, the raw text and the raw date fragment, so every number can be traced back to its page. When a value cannot be parsed it is left empty and flagged, never guessed. Sources, URLs, scrape dates and row counts are listed in `docs/sources.md`; every choice and its reason in `docs/decisions.md`. The time window is 2013-01 to 2026-10, i.e. from the AfD's founding to the scrape date.

The table below summarises the 17 collected files (71,124 rows in total). Three further event sources (taschenhirn.de, geopol.uk, English Wikipedia crisis list) were scraped, compared against the Wikipedia year pages and dropped; so was the occupation exit-poll page, which does not serve Q2: of their few day-dated rows most duplicate Wikipedia, and month- or year-dated rows cannot be placed on a weekly poll series.

| File | One row is | Rows | Variables (meaning) |
|---|---|---:|---|
| **Q1 outcome: polls (wahlrecht.de)** | | | |
| `surveys_bund` | one poll | 3,305 | institute (8), publication date, fieldwork period, sample size; vote share in % for CDU/CSU, SPD, Grüne, FDP, Linke, AfD, FW, BSW, Sonstige (NaN if not reported). About 220–280 polls per year, 2013-02 to 2026-10. |
| `surveys_land` | one state poll | 275 | same, plus state (14 states); Bundestag question asked in a state |
| `ltw_surveys` | one state-election poll | 1,304 | Landtagswahl question, 16 states, 27 institutes, 2013-01 to 2026-10; same layout plus BIW/BD, PIRATEN, SSW where above 5 % in at least one poll |
| **Q1 treatments and exposure** | | | |
| `events_wikipedia_jahr` | one event | 1,356 | list items of the "Ereignisse" section of the German Wikipedia year pages 2013–2026, politics, economy and disaster subsections only: date (1,269 day-exact, 58 month-only, 29 unparsable), subsection (e.g. D-A-CH, Europa, Terroranschläge), text, Wikipedia link titles, provenance |
| `tagesschau_archiv` | one news teaser | 60,955 | tagesschau.de archive 2013-01 to 2026-10: date, time, section (inland, ausland, wirtschaft = 85 %), headline, teaser text, URL |
| **Q1 controls (monthly)** | | | |
| `controls_monthly` | one month | 165 | Eurostat: ILO unemployment rate (seasonally adjusted), first-time asylum applicants |
| `regierungszufriedenheit` | one month | 133 | ARD DeutschlandTrend: satisfaction with the federal government, four categories in % |
| **Ground truth: official results (Bundeswahlleiterin)** | | | |
| `btw_results` | election × area | 68 | 2013, 2017, 2021, 2025; 16 states + federal; eligible voters, turnout, first and second votes per party in % |
| `btw_wahlkreise` | election × constituency | 1,196 | same per constituency (299 each), plus party of the directly elected candidate |
| `ltw_results` | one state election | 44 | party shares in %, 2013–2026 |
| **Q2: voter profile and structure** | | | |
| `btw_gender_age` | election × gender × age | 96 | representative election statistics: second-vote share per party |
| `btw_education`, `ltw_age`, `ltw_gender`, `ltw_education` | election × group | 12 / 176 / 24 / 48 | Forschungsgruppe Wahlen exit polls, read from PDF charts: share per party by education, age, gender |
| `tagesschau_umfrage-*` | election × chart × group | 771 | Infratest dimap exit-poll charts for the four federal elections (age, gender, education, AfD voters), long format |
| `btw_strukturdaten` | election × constituency | 1,196 | official structural data: density, foreigner share, six age shares, school leavers, income, GDP, unemployment, SGB II recipients (22 variables) |

*Collected datasets (`data/interim/`, state 2026-10-23). Full column lists and an ER diagram in `docs/data_model.md`, part 1.*

## Preprocessing done and choices made

- **Window and parties.** Everything is cut to 2013 onward at scrape time. All tables carry every party, not only the AfD, under one set of names (CDU and CSU summed to CDU/CSU; FW and BSW split out of "Sonstige" where the source lists them), so shifts between parties, e.g. AfD to BSW in 2024, stay visible.
- **Dates parsed from text, with a precision flag.** Wikipedia items have no date field; the date is parsed from the start of the item text with fixed patterns ("15. April", "7.–9. Mai", "Ab August"). Each row keeps the matched fragment and a flag `tag` / `monat` / `fehlt`; month-only items get the first of the month, unparsable ones stay empty. Nothing is looked up.
- **Empty is not zero.** A party that did not stand in a constituency is left empty, as in the source, where real zeros are written "0". Variables a year does not publish (2013: income, GDP) stay NaN.
- **Keywords from the source, not from us.** The Wikipedia link titles inside an item are stored as its keywords so the later matching to news is reproducible; the two sources are only ever joined in the analysis, never merged.
- **News archive thins out backwards.** The archive has about 1,300 teasers in 2013 and 13,700 in 2025, so coverage will only be compared as a share within a week, never as a count. Only the sections inland, ausland and wirtschaft are politically relevant; 841 rows link to other ARD sites and fall outside these sections.
- **Controls from Eurostat** rather than Bundesagentur and BAMF: one consistent monthly API series since 2013. The ILO rate runs 1.5–2 points below the German headline rate; asylum counts are first-time applications, which lag the 2015 arrivals by months and peak in 2016-08. Both caveats go into the interpretation.
- **Exit polls from PDFs and chart alt texts.** Group labels are kept as printed (e.g. 16–29 vs 18–29); harmonising them is an analysis decision. 2017 has no education chart and is missing, not interpolated.
- **Left as scraped.** Poll files keep the German date format, thousands separators and percent signs of wahlrecht.de; converting is a one-line processing step and the printed values stay checkable against the source.

## Planned processing (not part of this deliverable)

Polls will be averaged per ISO week by the midpoint of their fieldwork period, with per-institute house effects removed. For each Wikipedia event, the share of political teasers in its week that carry word stems bursting above their long-run frequency will measure exposure; an event without any coverage is kept as a comparison case, not dropped. Topics that dominate the news without a Wikipedia entry (typically domestic debates) will be summarised per week from the same bursts and treated as a second, coarser treatment set. The classification of events into international and national is still open; it will follow a rule derived from the source structure, never hand labelling. Limitations we already know: teasers only, three sections only, a thin archive before 2019, and Wikipedia's choice of events is itself a curation.

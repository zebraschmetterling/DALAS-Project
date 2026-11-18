"""
Preprocess German federal election (Bundestagswahl) results.

This script reads two interim data files:
    - data/interim/btw_results.csv     (results per federal state, possibly incl. a federal total)
    - data/interim/btw_wahlkreise.csv  (results per constituency / Wahlkreis)

and combines them into one tidy table, data/processed/btw_results.csv, with the schema:

    Date | Level | State | Constituency number | Constituency | Direct mandate | Voter turnout
         | First vote <Party> | Second vote <Party>

    - Date:            exact election day (e.g. 2013-09-22)
    - Level:           'federal', 'state' or 'constituency'
    - State:           name of the federal state (only for 'state' and 'constituency' rows)
    - Constituency number: official number of the constituency ("Nr", only for 'constituency' rows)
    - Constituency:    name of the constituency (only for 'constituency' rows)
    - Direct mandate:  party label of the directly elected candidate (only for 'constituency' rows)
    - Voter turnout:   voters / eligible voters * 100
    - First vote <P>:  first votes of party P / valid first votes * 100
    - Second vote <P>: second votes of party P / valid second votes * 100

Party names are replaced by the labels defined in label.py (get_party_label) and the
numeric state codes in the "gehört zu" column are resolved via label.py (get_bundesland).
The votes of the Freie Wähler (FW) are counted as "Sonstige" (other parties).
If the state-level file contains no federal total for an election year, the federal row
is computed by summing up all states of that year.
"""

from pathlib import Path

import pandas as pd

from labels import get_bundesland, get_party_label


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

# Project root is two levels above this file (src/preprocessing/btw_results.py)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

STATES_INPUT = PROJECT_ROOT / "data" / "interim" / "btw_results.csv"
CONSTITUENCIES_INPUT = PROJECT_ROOT / "data" / "interim" / "btw_wahlkreise.csv"
OUTPUT_FILE = PROJECT_ROOT / "data" / "processed" / "btw_results.csv"

# Exact election day for every Bundestag election year in the data
ELECTION_DATES = {
    2013: "2013-09-22",
    2017: "2017-09-24",
    2021: "2021-09-26",
    2025: "2025-02-23",
}

# Values of the "Gebiet" column that denote the federal total instead of a single state
FEDERAL_AREA_NAMES = {"bundesgebiet", "deutschland", "bund"}

# Prefixes of the vote count columns in the interim files
FIRST_VOTE_PREFIX = "Erststimmen "
SECOND_VOTE_PREFIX = "Zweitstimmen "

# Parties whose votes are added to another party column (source -> target)
MERGED_PARTIES = {"FW": "Sonstige"}

# Number of decimal places for all percentage values
DECIMALS = 2

# Sort order of the levels in the output table
LEVEL_ORDER = {"federal": 0, "state": 1, "constituency": 2}


# --------------------------------------------------------------------------------------
# Helper functions
# --------------------------------------------------------------------------------------

def merge_parties(raw: pd.DataFrame) -> pd.DataFrame:
    """Add the votes of every party in MERGED_PARTIES to its target party and drop its columns."""
    raw = raw.copy()
    for source, target in MERGED_PARTIES.items():
        for prefix in (FIRST_VOTE_PREFIX, SECOND_VOTE_PREFIX):
            source_column = prefix + source
            target_column = prefix + target
            if source_column not in raw.columns:
                continue
            if target_column in raw.columns:
                # Missing values count as 0, but the result stays empty if both parties are missing
                raw[target_column] = raw[[target_column, source_column]].sum(axis=1, min_count=1)
            else:
                raw[target_column] = raw[source_column]
            raw = raw.drop(columns=source_column)

    # A direct mandate won by a merged party is assigned to the target party as well
    if "Direkt gewählter Abgeordnete" in raw.columns:
        raw["Direkt gewählter Abgeordnete"] = raw["Direkt gewählter Abgeordnete"].replace(MERGED_PARTIES)

    return raw


def get_parties(*frames: pd.DataFrame) -> list[str]:
    """Return all party names that appear in the vote columns, in order of first appearance."""
    parties: list[str] = []
    for frame in frames:
        for column in frame.columns:
            for prefix in (FIRST_VOTE_PREFIX, SECOND_VOTE_PREFIX):
                # "Gültige Erststimmen" does not start with the prefix, so only party columns match
                if column.startswith(prefix):
                    party = column[len(prefix):]
                    if party not in parties:
                        parties.append(party)
    return parties


def build_party_labels(parties: list[str]) -> dict[str, str]:
    """Map every raw party name to its label from label.py (falls back to the raw name)."""
    labels: dict[str, str] = {}
    for party in parties:
        label = get_party_label(party)
        if label is None:
            print(f"WARNING: No label found for party '{party}', using the raw name instead.")
            label = party
        labels[party] = label
    return labels


def percentage(part: pd.Series, total: pd.Series) -> pd.Series:
    """Compute part / total * 100; a total of 0 or a missing part results in an empty value."""
    return (part / total.where(total != 0) * 100).round(DECIMALS)


def to_election_date(years: pd.Series) -> pd.Series:
    """Translate election years into the exact election day; abort on unknown years."""
    unknown_years = sorted(set(years) - set(ELECTION_DATES))
    if unknown_years:
        raise ValueError(f"No election date defined for year(s): {unknown_years}")
    return years.map(ELECTION_DATES)


def compute_shares(
    raw: pd.DataFrame,
    parties: list[str],
    party_labels: dict[str, str],
) -> pd.DataFrame:
    """Compute voter turnout and the first / second vote share of every party."""
    result = pd.DataFrame(index=raw.index)
    result["Voter turnout"] = percentage(raw["Wählende"], raw["Wahlberechtigte"])

    # First vote shares, relative to all valid first votes
    for party in parties:
        column = FIRST_VOTE_PREFIX + party
        # A party that is missing in a file (or did not run) gets an empty value
        votes = raw[column] if column in raw.columns else pd.Series(float("nan"), index=raw.index)
        result[f"First vote {party_labels[party]}"] = percentage(votes, raw["Gültige Erststimmen"])

    # Second vote shares, relative to all valid second votes
    for party in parties:
        column = SECOND_VOTE_PREFIX + party
        votes = raw[column] if column in raw.columns else pd.Series(float("nan"), index=raw.index)
        result[f"Second vote {party_labels[party]}"] = percentage(votes, raw["Gültige Zweitstimmen"])

    return result


def label_direct_mandate(party: object) -> object:
    """Translate the party of the direct mandate into its label (keeps empty values empty)."""
    if pd.isna(party):
        return pd.NA
    label = get_party_label(str(party).strip())
    if label is None:
        print(f"WARNING: No label found for direct mandate party '{party}', using the raw name instead.")
        return party
    return label


def resolve_state(code: object) -> object:
    """Translate the "gehört zu" state code into the name of the federal state."""
    if pd.isna(code):
        return pd.NA
    name = get_bundesland(str(code).strip())
    if name is None:
        print(f"WARNING: Unknown state code '{code}' in column 'gehört zu'.")
        return pd.NA
    return name


# --------------------------------------------------------------------------------------
# Building the three levels
# --------------------------------------------------------------------------------------

def build_federal_and_state_rows(
    states_raw: pd.DataFrame,
    parties: list[str],
    party_labels: dict[str, str],
) -> pd.DataFrame:
    """Create the 'federal' and 'state' rows from the state-level interim file."""
    is_federal = states_raw["Gebiet"].astype(str).str.strip().str.lower().isin(FEDERAL_AREA_NAMES)
    state_rows = states_raw[~is_federal]
    federal_rows = states_raw[is_federal]

    # Years without an explicit federal total: sum up all states of that year
    missing_years = sorted(set(state_rows["Jahr"]) - set(federal_rows["Jahr"]))
    if missing_years:
        print(f"No federal total found for {missing_years}, aggregating state results instead.")
        count_columns = state_rows.columns.drop(["Jahr", "Gebiet"])
        aggregated = (
            state_rows[state_rows["Jahr"].isin(missing_years)]
            .groupby("Jahr", as_index=False)[count_columns]
            # min_count=1 keeps a party empty if it did not run in any state that year
            .sum(min_count=1)
        )
        federal_rows = pd.concat([federal_rows, aggregated], ignore_index=True)

    # Federal level: no state, constituency or direct mandate
    federal = pd.DataFrame({
        "Date": to_election_date(federal_rows["Jahr"]),
        "Level": "federal",
        "State": pd.NA,
        "Constituency number": pd.NA,
        "Constituency": pd.NA,
        "Direct mandate": pd.NA,
    })
    federal = pd.concat([federal, compute_shares(federal_rows, parties, party_labels)], axis=1)

    # State level: the "Gebiet" column already contains the name of the state
    state = pd.DataFrame({
        "Date": to_election_date(state_rows["Jahr"]),
        "Level": "state",
        "State": state_rows["Gebiet"].str.strip(),
        "Constituency number": pd.NA,
        "Constituency": pd.NA,
        "Direct mandate": pd.NA,
    })
    state = pd.concat([state, compute_shares(state_rows, parties, party_labels)], axis=1)

    return pd.concat([federal, state], ignore_index=True)


def build_constituency_rows(
    constituencies_raw: pd.DataFrame,
    parties: list[str],
    party_labels: dict[str, str],
) -> pd.DataFrame:
    """Create the 'constituency' rows from the constituency-level interim file."""
    constituency = pd.DataFrame({
        "Date": to_election_date(constituencies_raw["Jahr"]),
        "Level": "constituency",
        "State": constituencies_raw["gehört zu"].map(resolve_state),
        "Constituency number": constituencies_raw["Nr"].str.strip(),
        "Constituency": constituencies_raw["Wahlkreis"].str.strip(),
        "Direct mandate": constituencies_raw["Direkt gewählter Abgeordnete"].map(label_direct_mandate),
    })
    return pd.concat([constituency, compute_shares(constituencies_raw, parties, party_labels)], axis=1)


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------

def main() -> None:
    print(f"Reading state results from {STATES_INPUT}")
    states_raw = pd.read_csv(STATES_INPUT)

    print(f"Reading constituency results from {CONSTITUENCIES_INPUT}")
    # Read the state code as text, because get_bundesland expects a string
    constituencies_raw = pd.read_csv(CONSTITUENCIES_INPUT, dtype={"gehört zu": str, "Nr": str})

    # Count the votes of merged parties (e.g. FW) as "Sonstige"
    for source, target in MERGED_PARTIES.items():
        print(f"Adding the votes of '{source}' to '{target}'")
    states_raw = merge_parties(states_raw)
    constituencies_raw = merge_parties(constituencies_raw)

    # Collect all parties of both files and translate them into their labels
    parties = get_parties(states_raw, constituencies_raw)
    party_labels = build_party_labels(parties)
    print(f"Found {len(parties)} parties: {', '.join(party_labels.values())}")

    # Build the three levels and stack them into one table
    result = pd.concat(
        [
            build_federal_and_state_rows(states_raw, parties, party_labels),
            build_constituency_rows(constituencies_raw, parties, party_labels),
        ],
        ignore_index=True,
    )

    # Sort by election, then federal -> state -> constituency (stable sort keeps the input order within a level)
    result = result.sort_values(
        by=["Date", "Level"],
        key=lambda column: column.map(LEVEL_ORDER) if column.name == "Level" else column,
        kind="stable",
    ).reset_index(drop=True)

    # Write the processed file (empty values are written as empty cells)
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUTPUT_FILE, index=False)

    # Short summary for the console
    print(f"Wrote {len(result)} rows to {OUTPUT_FILE}")
    for level, count in result["Level"].value_counts().reindex(LEVEL_ORDER.keys(), fill_value=0).items():
        print(f"  {level}: {count} rows")


if __name__ == "__main__":
    main()

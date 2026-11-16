"""
========= INSTITUTES TO LABEL =========
"""

INSTITUTE_TO_LABEL : dict[str, str] = {
    "allensbach" : "A",
    "verian" : "B",
    "kantar public" : "B",
    "emnid": "B",
    "forsa": "C",
    "forschungsgruppe wahlen": "D",
    "forsch’gr. wahlen": "D",
    "gms": "E",
    "infratest dimap": "F",
    "insa": "G",
    "yougov": "H",
}

def get_institute_label (name: str) -> str | None:
    if not name:
        return None
    name = name.strip().lower()
    if name in INSTITUTE_TO_LABEL:
        return INSTITUTE_TO_LABEL[name]
    else:
        return None

"""
========= PARTIES TO LABEL ========= 
"""

# todo: add regional parties if needed
PARTY_TO_LABEL : dict[str, str] = {
    "CDU": "CDU",
    "CDU/CSU": "CDU",
    "CSU": "CSU",
    "SPD": "SPD",
    "GRÜNE": "Greens",
    "Grüne": "Greens",
    "Linke": "Linke",
    "LINKE": "Linke",
    "FDP": "FDP",
    "AFD": "AfD",
    "AfD": "AfD",
    "BSW": "BSW",
    "FW": "FW",
    "Sonstige": "Other",
}

def get_party_label (name: str) -> str | None:
    if not name:
        return None
    name = name.strip()
    if name in PARTY_TO_LABEL:
        return PARTY_TO_LABEL[name]
    else:
        return None


"""
========= BUNDESLAND EAST/WEST CLASSIFICATION =========
"""

BUNDESLAND_CLASSIFICATION : dict[str, str] = {
    "baden-württemberg" : "W",
    "bayern" : "W",
    "berlin" : "E",
    "brandenburg": "E",
    "bremen": "W",
    "hamburg": "W",
    "hessen": "W",
    "mecklenburg-vorpommern": "E",
    "niedersachsen": "W",
    "nordrhein-westfalen": "W",
    "rheinland-Pfalz": "W",
    "saarland": "W",
    "sachsen": "E",
    "sachsen-anhalt": "E",
    "schleswig-holstein": "W",
    "thüringen": "E",

}

def get_bundesland_classification (name: str) -> str | None:
    if not name:
        return None
    name = name.strip().lower()
    if name in BUNDESLAND_CLASSIFICATION:
        return BUNDESLAND_CLASSIFICATION[name]
    else:
        return None

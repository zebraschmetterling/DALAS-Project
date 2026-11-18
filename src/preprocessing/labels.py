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
    "rheinland-pfalz": "W",
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


"""
========= BUNDESLAND NUMBER =========
"""

BUNDESLAND_NUMBER: dict[str, str] = {
   "08" : "Baden-Württemberg",
   "8" : "Baden-Württemberg",
   "09" : "Bayern",
   "9" : "Bayern",
   "11" : "Berlin",
   "12" : "Brandenburg",
   "04" : "Bremen",
   "4": "Bremen",
   "02" : "Hamburg",
   "2": "Hamburg",
   "06" : "Hessen",
   "6": "Hessen",
   "13" : "Mecklenburg-Vorpommern",
   "03" : "Niedersachsen",
   "3": "Niedersachsen",
   "05" : "Nordrhein-Westfalen",
   "5" : "Nordrhein-Westfalen",
   "07" : "Rheinland-Pfalz",
   "7" : "Rheinland-Pfalz",
   "10" : "Saarland",
   "14" : "Sachsen",
   "15" : "Sachsen-Anhalt",
   "01" : "Schleswig-Holstein",
   "1": "Schleswig-Holstein",
   "16" : "Thüringen",

}


def get_bundesland(name: str) -> str | None:
    if not name:
        return None
    name = name.strip().lower()
    if name in BUNDESLAND_NUMBER:
        return BUNDESLAND_NUMBER[name]
    else:
        return None



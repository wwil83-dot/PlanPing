"""
Work-type classifier for planning applications (keyword rules, no LLM).

classify(description, application_type="") -> one of WORK_TYPES keys.
First matching rule wins, so order matters: specific before generic.
Pure function, no DB — tested by test_worktype.py-style asserts at the bottom
(run `python worktype.py`).
"""
import re

WORK_TYPES = {
    "loft_conversion":   "Loft conversion",
    "rear_extension":    "Rear extension",
    "side_extension":    "Side extension",
    "two_storey":        "Two-storey extension",
    "extension":         "Extension (other)",
    "new_dwelling":      "New home(s)",
    "change_of_use":     "Change of use / conversion",
    "outbuilding":       "Garage, annexe or outbuilding",
    "conservatory":      "Conservatory / orangery",
    "solar":             "Solar panels / energy",
    "windows_doors":     "Windows and doors",
    "roof":              "Roof works",
    "basement":          "Basement",
    "driveway":          "Driveway / hardstanding / fencing",
    "demolition":        "Demolition",
    "commercial":        "Commercial / industrial building",
    "signage":           "Advertisements / signage",
    "trees":             "Tree works",
    "admin":             "Conditions / certificates / admin",
    "other":             "Other",
}

# (work_type, compiled regex) — evaluated in order against lower-cased text.
_R = lambda p: re.compile(p, re.I)
RULES = [
    ("trees",         _R(r"\b(tree preservation|tpo\b|works? to trees?|fell(ing)? (of )?(a |the )?\w*\s*tree|crown (reduction|lift)|t\d+ (oak|ash|beech|lime|sycamore))")),
    ("signage",       _R(r"\b(advertisement consent|display of (an? )?(illuminated )?(sign|advert)|fascia sign|totem sign|hoarding)")),
    ("admin",         _R(r"\b(discharge of condition|approval of details? (reserved|pursuant)|non[- ]?material amendment|variation of condition|removal of condition|certificate of lawfulness|lawful development certificate|prior notification|screening opinion|scoping opinion|pre-?application|tree/hedge notice|section 73|s73|s\.73|submission of details)")),
    ("demolition",    _R(r"^(?!.*\b(erection|construction|replacement|redevelopment|new)\b).*\b(demolition of|demolish|prior approval.{0,20}demolition)")),
    ("loft_conversion", _R(r"\b(loft conversion|roof conversion|rear dormer|side dormer|front dormer|dormer|roof lights?|rooflights?|hip[- ]to[- ]gable|convert(ing)? (the )?(loft|roof ?space)|habitable roof|loft extension)")),
    ("solar",         _R(r"\b(solar|photovoltaic|\bpv\b|heat pump|air source|ground source|battery storage|ev charg|wind turbine|biomass)")),
    ("basement",      _R(r"\b(basement|lower ground|cellar|subterranean)")),
    ("conservatory",  _R(r"\b(conservator|orangery|sun ?room|garden room|glazed (extension|roof))")),
    ("two_storey",    _R(r"\b(two[- ]storey|2[- ]storey|double[- ]storey|first floor (rear |side )?extension|two storey)")),
    ("rear_extension",_R(r"\b(single[- ]storey rear|rear extension|rear single|extension to (the )?rear|erection of (a )?(single storey )?(rear|extension to rear)|larger home extension|rear (ground floor )?extension|kitchen extension)")),
    ("side_extension",_R(r"\b(side extension|extension to (the )?side|side and rear|wrap[- ]around extension)")),
    ("change_of_use", _R(r"\b(change of use|conversion (of|to|from)|convert(ing)? (the )?[\w\s\-]{0,40} (to|into) (a )?(\d+ )?(dwelling|residential|flat|apartment|house|hmo|home)|class (ma|q|r|s|o|c3|e|b8)\b|prior approval.{0,40}(change|class)|hmo|house in multiple|subdivision|sub-?divide|flats? above|office to residential|agricultural (building|barn) to)")),
    ("new_dwelling",  _R(r"\b(erection of (a |an |\d+ |one |two |three |four |five |six )?(\w+[- ])?(new )?(dwelling|house|bungalow|home|detached|semi|flats?|apartment|residential|townhouse)|construction of (a |an |\d+ )?(new )?(dwelling|house|bungalow|home|flats?|apartment)|new (dwelling|house|bungalow|home)s?|(\d+|one|two|three|four|five|six|seven|eight|nine|ten) (new )?(dwellings?|houses|homes|flats|apartments|residential units)|residential development|housing development|self[- ]?build|replacement dwelling|outline.{0,30}(residential|dwelling)|up to \d+ (dwellings|homes))")),
    ("outbuilding",   _R(r"\b(garage|car ?port|annexe|annex\b|outbuilding|summer ?house|shed\b|studio\b|home office|stable|barn\b|log cabin|granny flat|ancillary (accommodation|building)|swimming pool|gym\b|pool house)")),
    ("windows_doors", _R(r"\b(replacement (of )?(upvc )?(windows?|doors?)|new (windows?|doors?)|window replacement|install(ation of)? (new )?(windows?|doors?)|bi[- ]fold|french doors|shop ?front|fenestration|double[- ]glaz)")),
    ("roof",          _R(r"\b(re-?roof|roof (replacement|repair|covering|alteration|extension|works?)|replacement roof|chimney|slate|new roof|raising (of )?(the )?roof|roof height)")),
    ("driveway",      _R(r"\b(driveway|drive way|dropped kerb|vehicle crossover|hardstanding|hard standing|parking (space|area)|fence\b|fencing|boundary (wall|treatment)|gates?\b|retaining wall|front garden|patio|decking|raised terrace|landscaping)")),
    ("extension",     _R(r"\b(extension|extend|erection of (a )?(single|two)|first floor|second floor|additional storey|porch|alterations? (to|and)|internal alterations?|refurbish|renovat)")),
    ("commercial",    _R(r"\b(warehouse|industrial|office|retail|shop|restaurant|cafe|café|pub\b|hotel|class (e|b2|b8)|commercial|unit \d+|storage and distribution|data centre|school|hospital|care home|nursery|gym\b|petrol|car (wash|showroom)|takeaway|drive[- ]?thr)")),
]

def classify(description, application_type=""):
    t = f"{description or ''} {application_type or ''}".lower()
    if not t.strip():
        return "other"
    for wt, rx in RULES:
        if rx.search(t):
            return wt
    return "other"

# Which trades want which work types (used by the patch checker). A work type
# can serve several trades — a loft conversion is of interest to a builder,
# roofer, electrician, plasterer and so on.
TRADES = {
    "general_builder": ("Builders",              ["loft_conversion","rear_extension","side_extension","two_storey","extension","new_dwelling","change_of_use","outbuilding","conservatory","basement"]),
    "architect":       ("Architects & designers", ["loft_conversion","rear_extension","side_extension","two_storey","extension","new_dwelling","change_of_use","basement","conservatory"]),
    "roofer":          ("Roofers",               ["roof","loft_conversion","two_storey","rear_extension","side_extension","new_dwelling","outbuilding"]),
    "electrician":     ("Electricians",          ["solar","loft_conversion","rear_extension","side_extension","two_storey","new_dwelling","change_of_use","basement"]),
    "plumber_heating": ("Plumbers & heating",    ["solar","loft_conversion","rear_extension","side_extension","two_storey","new_dwelling","change_of_use","basement"]),
    "plasterer":       ("Plasterers",            ["loft_conversion","rear_extension","side_extension","two_storey","extension","new_dwelling","change_of_use","basement"]),
    "joiner_carpenter":("Joiners & carpenters",  ["loft_conversion","rear_extension","side_extension","two_storey","extension","windows_doors","conservatory","outbuilding","new_dwelling"]),
    "glazier":         ("Windows, doors & glazing", ["windows_doors","conservatory","rear_extension","side_extension","two_storey","extension","new_dwelling"]),
    "solar_installer": ("Solar & renewables",    ["solar","new_dwelling"]),
    "landscaper":      ("Landscapers & groundworks", ["driveway","outbuilding","new_dwelling","rear_extension","side_extension"]),
}

if __name__ == "__main__":
    tests = {
        "Erection of a single storey rear extension": "rear_extension",
        "Loft conversion with rear dormer": "loft_conversion",
        "Demolition of existing garage and erection of 2 dwellings": "new_dwelling",
        "Demolition of derelict outbuilding": "demolition",
        "Erection of 3 detached dwellings with associated parking": "new_dwelling",
        "Change of use from office (Class E) to 4 flats": "change_of_use",
        "Installation of solar panels to roof": "solar",
        "Replacement windows and doors": "windows_doors",
        "Discharge of condition 4 of 21/00123/FUL": "admin",
        "Works to a TPO oak tree T1": "trees",
        "Two storey side extension": "two_storey",
        "Single storey side extension": "side_extension",
        "Construction of detached garage": "outbuilding",
        "Advertisement consent for fascia sign": "signage",
        "": "other",
    }
    bad = [(d, classify(d), w) for d, w in tests.items() if classify(d) != w]
    for b in bad: print("MISMATCH", b)
    print("worktype self-test:", "OK" if not bad else f"{len(bad)} mismatches")

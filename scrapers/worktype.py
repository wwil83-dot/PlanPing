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
    "listed_building":   "Listed building works",
    "internal":          "Internal alterations / refurbishment",
    "unknown":           "No description available",
    "other":             "Other",
}

# (work_type, compiled regex) — evaluated in order against lower-cased text.
_R = lambda p: re.compile(p, re.I)
_NOT_ADD = r"(?:(?!extension|garage|porch|shed|fence|wall\b|store\b|outbuilding|conservatory|alteration|ramp)[^.]){0,80}"
_DW = r"(?:dwellings?|dwellinghouses?|houses?|bungalows?|homes?|townhouses?|apartments?|flats?|cottages?)"
RULES = [
    # --- things that are not building work for a trade ---
    ("trees",    _R(r"^(?!.*\b(erection|construction|extension|dwellings?|conversion|demolition|storey)\b).*\b(\bt\d+\b[^.]{0,50}\b(remove|fell|prune|reduce|pollard)|tree preservation|tpo\b|works? to trees?|trees?\b|hedges?\b|high hedge|stump|pollard|coppic|crown (reduction|lift|thin)|\bfell(ing)?\b)")),
    ("signage",  _R(r"\b(advertisement|advertising|illuminated|signage|\bsigns?\b|fascia|totem|hoarding|48[- ]sheet|6[- ]sheet|digital (display|screen)|display (panel|unit))")),
    ("admin",    _R(r"\b(discharge of condition|approval of details?|non[- ]?material amendment|material amendment|variation of condition|removal of condition|(required|pursuant to|in relation to|compliance with|details of[\w\s]{0,40}) conditions?\b|condition \d+|certificate of lawfulness|lawful development|prior notification|screening opinion|scoping opinion|pre-?application|section 73|\bs\.?73|submission of details|(amendment|modification|variation) (to|of)\b|previous(ly)? (approved|approval|consent|permission)|opening hours|hours of (use|opening|operation)|extension of time|renewal of (planning )?permission)")),
    ("demolition", _R(r"^(?!.*\b(erection|construction|replacement|redevelopment|new)\b).*\b(demolition of|demolish|prior approval.{0,20}demolition)")),
    # --- building work ---
    ("loft_conversion", _R(r"\b(loft conversion|roof conversion|dormers?|roof ?lights?|hip[- ]to[- ]gable|convert(ing)? (the )?(loft|roof ?space)|habitable roof|loft extension|loft)")),
    ("solar",    _R(r"\b(solar|photovoltaic|\bpv\b|heat pump|air source|ground source|battery storage|ev charg|wind turbine|biomass)")),
    ("basement", _R(r"\b(basement (extension|conversion|excavation|storey|level|development|flat|room|garage|area)|excavat\w+[^.]{0,60}basement|(new|additional|extend\w*|enlarg\w*|form\w*|construct\w*|creat\w*|convert\w*|dig\w*)[^.]{0,25}\b(basement|cellar)\b|subterranean)")),
    ("conservatory", _R(r"\b(conservator|orangery|sun ?room|garden room|glazed (extension|roof))")),
    ("two_storey", _R(r"\b((two|2|double)[- ]storey[^.]{0,40}(extension|addition|rear|side|extend)|(extension|addition)[^.]{0,30}(two|2)[- ]storey|first floor (rear |side )?extension)")),
    ("rear_extension", _R(r"\b(single[- ]storey rear|rear extension|rear single|extension to (the )?rear|to rear\b|larger home extension|rear (ground floor )?extension|kitchen ext(ension)?\b)")),
    ("side_extension", _R(r"\b(side extension|extension to (the )?side|side and rear|wrap[- ]around extension)")),
    ("change_of_use", _R(r"\b(change of use|conversion (of|to|from|and)|convert(ing)? [^.]{0,60} (to|into) [^.]{0,30}" + _DW + r"|class (ma|q|r|s|o|c3|e|b8)\b|prior approval.{0,40}(change|class)|hmo|house in multiple|subdivision|sub-?divid|amalgamation|flats? above|office to residential|agricultural (building|barn) to|conversion[^.]{0,80}(into|to)[^.]{0,30}" + _DW + r")")),
    ("new_dwelling", _R(r"\b((erection|construction|build|development) of" + _NOT_ADD + r"\b" + _DW + r"|new[- ]build|new " + _DW + r"|(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)[\s\-]?(no\.?\s*)?(\w+[- ]){0,4}(dwellings?|houses|homes|flats|apartments|bungalows|cottages|units)\b|(pair|row|terrace|block) of[\w\s\-]{0,20}" + _DW + r"s?|detached (houses|dwellings|homes|bungalows)|(houses|dwellings|bungalows) (and|with) |residential development|housing (development|scheme)|self[- ]?build|replacement dwelling|outline[^.]{0,40}(residential|dwelling|housing)|permission in principle|up to \d+ (dwellings|homes))")),
    ("extension", _R(r"\b(extension|extend|\bext\b)")),
    ("outbuilding", _R(r"\b(garages?|car ?ports?|annexe|annex\b|outbuildings?|summer ?houses?|sheds?\b|studio(?! ?flats?)|home office|stables?|barns?\b|log cabins?|granny flat|ancillary (accommodation|building)|swimming pool|gym\b|pool house|greenhouse|gazebo|store\b)")),
    ("windows_doors", _R(r"\b(replacement (of )?(upvc )?(windows?|doors?)|(new|replacement|install\w*)\b[^.]{0,30}\b(windows?|doors?|glazing)\b|window replacement|bi[- ]fold|french doors|shop ?front|fenestration|double[- ]glaz|secondary glazing)")),
    ("roof",     _R(r"\b(re-?roof|roof (replacement|repair|covering|alteration|extension|works?)|replacement roof|chimney|slate|new roof|raising (of )?(the )?roof|roof height|(replace|replacement|new|pitched|flat|alter\w*)[^.]{0,30}\broof\b|\broof\b[^.]{0,20}(replac|alter|repair))")),
    ("driveway", _R(r"\b(driveway|drive way|dropped kerb|vehicle crossover|hardstanding|hard standing|parking (space|area)|fence\b|fencing|boundary (wall|treatment)|gates?\b|retaining wall|front garden|patio|decking|raised terrace|terraces|landscaping|ramp\b|steps\b|wall\b)")),
    ("commercial", _R(r"\b(warehouse|industrial|office|retail|shop|restaurant|cafe|café|pub\b|hotel|class (e|b2|b8)|commercial|unit \d+|storage and distribution|data centre|school|hospital|care home|nursery|petrol|car (wash|showroom)|takeaway|drive[- ]?thr|club|premises|mast\b|antennae?|telecom|church|community (hall|centre))")),
    ("listed_building", _R(r"\b(listed building|conservation area consent)")),
    ("internal", _R(r"\b(internal|interior|refurbish\w*|renovat\w*|renewal|kitchens?|bathrooms?|staircase|porch|alterations?|stud (wall|partition)|remodel)")),
]

def classify(description, application_type=""):
    t = f"{description or ''} {application_type or ''}".lower()
    if not t.strip():
        return "unknown"
    for wt, rx in RULES:
        if rx.search(t):
            return wt
    return "other"

# Which trades want which work types (used by the patch checker). A work type
# can serve several trades — a loft conversion is of interest to a builder,
# roofer, electrician, plasterer and so on.
TRADES = {
    "general_builder": ("Builders",              ["internal","listed_building","loft_conversion","rear_extension","side_extension","two_storey","extension","new_dwelling","change_of_use","outbuilding","conservatory","basement"]),
    "architect":       ("Architects & designers", ["loft_conversion","rear_extension","side_extension","two_storey","extension","new_dwelling","change_of_use","basement","conservatory"]),
    "roofer":          ("Roofers",               ["roof","loft_conversion","two_storey","rear_extension","side_extension","new_dwelling","outbuilding"]),
    "electrician":     ("Electricians",          ["internal","solar","loft_conversion","rear_extension","side_extension","two_storey","new_dwelling","change_of_use","basement"]),
    "plumber_heating": ("Plumbers & heating",    ["internal","solar","loft_conversion","rear_extension","side_extension","two_storey","new_dwelling","change_of_use","basement"]),
    "plasterer":       ("Plasterers",            ["internal","loft_conversion","rear_extension","side_extension","two_storey","extension","new_dwelling","change_of_use","basement"]),
    "joiner_carpenter":("Joiners & carpenters",  ["internal","loft_conversion","rear_extension","side_extension","two_storey","extension","windows_doors","conservatory","outbuilding","new_dwelling"]),
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
        "": "unknown",
        "Construction of single storey dwelling": "new_dwelling",
        "Construction of a pair of semi detached dwellings": "new_dwelling",
        "4 Detached Houses": "new_dwelling",
        "Eight-3 Bedroom Tce Houses": "new_dwelling",
        "Erection of 2 no. 1.5 storey semi detached dwellings": "new_dwelling",
        "Residential Development": "new_dwelling",
        "New build dwelling on vacant land incorporating ground floor single bed dwelling and two first floor studio flats": "new_dwelling",
        "Conversion and partial reconstruction of existing barn into two dwellings": "change_of_use",
        "Amalgamation of 2 flats into 1 at second floor level": "change_of_use",
        "T1 Pink Chestnut - Remove": "trees",
        "High hedge complaint": "trees",
        "Robinia Frisia (T1) - fell to ground level and removal of stump.": "trees",
        "1 x 48 sheet with bespoke panelling, freestanding digital advertising display unit": "signage",
        "Internally illuminated signs": "signage",
        "Kitchen Ext": "rear_extension",
        "Pitched roof to replace existing flat roof & alterations.": "roof",
        "New front & rear door": "windows_doors",
        "Alternative staircase location to previous approval": "admin",
        "Details of new doors required by condition 1b of listed building consent": "admin",
        "Excavation to extend the current basement to match footprint of ground floor.": "basement",
        "Replacement of metal framed window at front lower ground floor level": "windows_doors",
        "Detach Garage And Kitchen , Dining Room Extension": "extension",
        "Extension to form lounge and additional bedroom": "extension",
        "Single storey garage": "outbuilding",
        "Renewal of kitchen & bathrooms and mechanical & electrical systems": "internal",
        "Construction of club premises and steward accommodation": "commercial",
        "Porch": "internal",
        "Erection of a garage to serve the dwelling": "outbuilding",
    }
    bad = [(d, classify(d), w) for d, w in tests.items() if classify(d) != w]
    for b in bad: print("MISMATCH", b)
    print("worktype self-test:", "OK" if not bad else f"{len(bad)} mismatches")

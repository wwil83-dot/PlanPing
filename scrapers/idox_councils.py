"""
PlanFind — Idox portal council list.

Format: (council_name_as_in_supabase_db, idox_base_url)
"""

# ---------------------------------------------------------------------------
# Hardcoded correct council IDs from the database.
# These BYPASS the unreliable name-matching lookup in the scraper.
# To add a new council: query Supabase for its id with:
#   SELECT id, name FROM councils WHERE name = 'Council Name';
# ---------------------------------------------------------------------------
COUNCIL_DB_IDS: dict[str, int] = {
    "Brighton and Hove City Council":           20,
    "Cheshire West and Chester Council":         27,
    "City of London":                            28,   # note: NOT "Corporation" in DB
    "Gloucester City Council":                   37,
    "Leeds City Council":                        48,
    "Plymouth City Council":                     59,
    "Wolverhampton City Council":                79,
    "Canterbury City Council":                   82,
    "Rochdale Borough Council":                  172,
    "Tameside Metropolitan Borough Council":     173,
    "Bradford Metropolitan District Council":    175,
    "Bolton Metropolitan Borough Council":       169,
    "Stockport Metropolitan Borough Council":    167,
    "Sefton Metropolitan Borough Council":       185,
    "Halton Borough Council":                    186,
    "North Tyneside Council":                    200,
    "Gateshead Council":                         198,
    "Durham County Council":                     201,
    "Cheltenham Borough Council":                213,
    "Ipswich Borough Council":                   215,
    "Knowsley Metropolitan Borough Council":     180,
    "Nottingham City Council":                   57,
    "Blackpool Council":                         15,
    "Babergh District Council":                  9,
    "Bedford Borough Council":                   12,
    "Solihull Metropolitan Borough Council":     192,
    "Portsmouth City Council":                   206,
    "London Borough of Tower Hamlets":           222,
    "London Borough of Newham":                  223,
    "London Borough of Waltham Forest":          224,
    "London Borough of Richmond upon Thames":    235,
    "London Borough of Brent":                   240,
}

IDOX_COUNCILS = [

    # -------------------------------------------------------------------------
    # GREATER MANCHESTER — all 9 boroughs (excl. Wigan, already on open data)
    # -------------------------------------------------------------------------
    # BROKEN — Manchester moved off Idox to Arcus BE (Salesforce):
    # arcusbe.manchester.gov.uk/pr/s/register-view?c__r=Arcus_BE_Public_Register
    # ("Manchester City Council",
    #  "https://pa.manchester.gov.uk/online-applications"),

    # BROKEN — Salford moved off Idox to Arcus BE (Salesforce):
    # salfordcitycouncil.my.site.com/pr/s/register-view?c__r=Arcus_BE_Public_Register
    # ("Salford City Council",
    #  "https://publicaccess.salford.gov.uk/online-applications"),

    ("Stockport Metropolitan Borough Council",
     "https://planning.stockport.gov.uk/PlanningData-live"),

    ("Trafford Council",
     "https://pa.trafford.gov.uk/online-applications"),

    ("Bolton Metropolitan Borough Council",
     "https://paplanning.bolton.gov.uk/online-applications"),

    # BROKEN — planning.bury.gov.uk redirects to Tameside's server; Bury needs research.
    # ("Bury Metropolitan Borough Council",
    #  "https://planning.bury.gov.uk/online-applications"),

    # NOTE: planningpa.oldham.gov.uk redirects to Rochdale's Idox server.
    # Using it here so Rochdale (id=172) gets its own data correctly.
    # Oldham's own correct URL needs research.
    ("Rochdale Borough Council",
     "https://planningpa.oldham.gov.uk/online-applications"),

    # BROKEN — original URL redirects to Rochdale's server instead of Oldham's own data.
    # Needs correct URL research before re-enabling.
    # ("Oldham Metropolitan Borough Council",
    #  "https://planningpa.oldham.gov.uk/online-applications"),

    # NOTE: planning.bury.gov.uk redirects to Tameside's Idox server.
    ("Tameside Metropolitan Borough Council",
     "https://planning.bury.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # YORKSHIRE
    # -------------------------------------------------------------------------
    ("Sheffield City Council",
     "https://planningapps.sheffield.gov.uk/online-applications"),

    ("Bradford Metropolitan District Council",
     "https://planning.bradford.gov.uk/online-applications"),

    ("Calderdale Metropolitan Borough Council",
     "https://portal.calderdale.gov.uk/online-applications"),

    # BROKEN — Kirklees uses a custom ASPX system (kirklees.gov.uk/beta/planning-applications), not Idox.
    # ("Kirklees Council",
    #  "https://www.kirklees.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # NORTH WEST (excl. Greater Manchester)
    # -------------------------------------------------------------------------
    # BROKEN — Liverpool moved off Idox to a non-Idox system (similar to Warrington's online.warrington.gov.uk).
    # ("Liverpool City Council",
    #  "https://planning.liverpool.gov.uk/online-applications"),

    # BROKEN — Wirral uses LAR/Built ID system (online.wirral.gov.uk/planning), not Idox.
    # ("Wirral Metropolitan Borough Council",
    #  "https://www.wirral.gov.uk/online-applications"),

    ("Knowsley Metropolitan Borough Council",
     "https://planapp.knowsley.gov.uk/online-applications"),

    # BROKEN — St Helens selected Idox Cloud (announced May 2026) but portal not yet live.
    # publicaccess.sthelens.gov.uk times out. Re-enable once migration completes.
    # ("St. Helens Metropolitan Borough Council",
    #  "https://publicaccess.sthelens.gov.uk/online-applications"),

    # BROKEN — Warrington moved off Idox to online.warrington.gov.uk/planning (non-Idox system).
    # ("Warrington Borough Council",
    #  "https://pa.warrington.gov.uk/online-applications"),

    ("Cheshire West and Chester Council",
     "https://pa.cheshirewestandchester.gov.uk/online-applications"),

    ("Cheshire East Council",
     "https://planning.cheshireeast.gov.uk/online-applications"),

    # Sefton's own Idox portal (confirmed — shows "Sefton Council" branding).
    ("Sefton Metropolitan Borough Council",
     "https://pa.sefton.gov.uk/online-applications"),

    # Halton's own Idox portal (pa.halton.gov.uk — different from Sefton's).
    ("Halton Borough Council",
     "https://pa.halton.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # WEST MIDLANDS
    # -------------------------------------------------------------------------
    # Coventry moved to planandregulatory.coventry.gov.uk (not Idox) — removed

    # BROKEN — /planning/search-planning-applications returns 404. Trying /online-applications.
    ("Wolverhampton City Council",
     "https://planningonline.wolverhampton.gov.uk/online-applications"),

    # BROKEN — Walsall uses Swift LG (planning.walsall.gov.uk/swift/...), not Idox.
    # ("Walsall Metropolitan Borough Council",
    #  "https://www.walsall.gov.uk/online-applications"),

    ("Sandwell Metropolitan Borough Council",
     "https://webcaps.sandwell.gov.uk/publicaccess"),

    # BROKEN — Dudley uses Agile Applications (planning.agileapplications.co.uk/dudley), not Idox.
    # ("Dudley Metropolitan Borough Council",
    #  "https://www.dudley.gov.uk/online-applications"),

    ("Solihull Metropolitan Borough Council",
     "https://publicaccess.solihull.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # EAST MIDLANDS
    # -------------------------------------------------------------------------
    # BROKEN — Leicester left Idox, now uses DEF Software (planning.leicester.gov.uk).
    # ("Leicester City Council",
    #  "https://publicaccess.leicester.gov.uk/online-applications"),

    ("Derby City Council",
     "https://eplanning.derby.gov.uk/online-applications"),

    ("Nottingham City Council",
     "https://publicaccess.nottinghamcity.gov.uk/online-applications"),

    ("Rutland County Council",
     "https://publicaccess.rutland.gov.uk/online-applications"),

    ("Stoke-on-Trent City Council",
     "https://planning.stoke.gov.uk/online-applications"),

    ("Blackpool Council",
     "https://idoxpa.blackpool.gov.uk/online-applications"),

    # BROKEN — Nottinghamshire uses a custom non-Idox system.
    # ("Nottinghamshire County Council",
    #  "https://publicaccess.nottinghamshire.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # NORTH EAST
    # -------------------------------------------------------------------------
    ("Newcastle City Council",
     "https://publicaccessapplications.newcastle.gov.uk/online-applications"),

    ("Sunderland City Council",
     "https://online-applications.sunderland.gov.uk/online-applications"),

    # North Tyneside has its own Idox server. public.gateshead.gov.uk is Gateshead's server.
    ("North Tyneside Council",
     "https://idoxpublicaccess.northtyneside.gov.uk/online-applications"),

    ("Gateshead Council",
     "https://public.gateshead.gov.uk/online-applications"),

    # BROKEN — South Tyneside uses Northgate (planning.southtyneside.info), not Idox.
    # ("South Tyneside Metropolitan Borough Council",
    #  "https://www.southtyneside.gov.uk/online-applications"),

    # BROKEN — publicaccess.durham.gov.uk is the official URL but shares Idox
    # infrastructure with Stockton-on-Tees; the monthly list returns Stockton's
    # applications rather than Durham's own. Commented out until a scoped URL is found.
    # ("Durham County Council",
    #  "https://publicaccess.durham.gov.uk/online-applications"),

    ("Middlesbrough Council",
     "https://www.middlesbrough.gov.uk/online-applications"),

    ("Stockton-on-Tees Borough Council",
     "https://www.stockton.gov.uk/online-applications"),

    ("Darlington Borough Council",
     "https://publicaccess.darlington.gov.uk/online-applications"),

    ("Northumberland County Council",
     "https://publicaccess.northumberland.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # YORKSHIRE AND THE HUMBER
    # -------------------------------------------------------------------------
    ("Leeds City Council",
     "https://publicaccess.leeds.gov.uk/online-applications"),

    ("Wakefield Metropolitan District Council",
     "https://planning.wakefield.gov.uk/online-applications"),

    ("Doncaster Metropolitan Borough Council",
     "https://planning.doncaster.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # SOUTH EAST
    # -------------------------------------------------------------------------
    ("Brighton and Hove City Council",
     "https://planningapps.brighton-hove.gov.uk/online-applications"),

    ("Southampton City Council",
     "https://planningpublicaccess.southampton.gov.uk/online-applications"),

    ("Portsmouth City Council",
     "https://publicaccess.portsmouth.gov.uk/online-applications"),

    ("Reading Borough Council",
     "https://planning.reading.gov.uk/online-applications"),

    # BROKEN — Milton Keynes moved to Arcus BE (be.milton-keynes.gov.uk), same as Manchester/Salford.
    # ("Milton Keynes City Council",
    #  "https://www.milton-keynes.gov.uk/online-applications"),

    ("Oxford City Council",
     "https://public.oxford.gov.uk/online-applications"),

    # BROKEN — Medway moved to Open Digital Planning (planningregister.org/medway), not Idox.
    # ("Medway Council",
    #  "https://publicaccess.medway.gov.uk/online-applications"),

    ("Isle of Wight Council",
     "https://publicaccess.iow.gov.uk/online-applications"),

    ("Canterbury City Council",
     "https://pa.canterbury.gov.uk/online-applications"),

    ("Gravesham Borough Council",
     "https://plan.gravesham.gov.uk/online-applications"),

    # NOTE: pa.midkent.gov.uk is a shared server for Maidstone, Swale and Tunbridge Wells.
    ("Maidstone Borough Council",
     "https://pa.midkent.gov.uk/online-applications"),

    ("Thanet District Council",
     "https://planning.thanet.gov.uk/online-applications"),

    ("Mid Sussex District Council",
     "https://pa.midsussex.gov.uk/online-applications"),

    ("Adur District Council",
     "https://planning.adur-worthing.gov.uk/online-applications"),

    ("South Downs National Park Authority",
     "https://planningpublicaccess.southdowns.gov.uk/online-applications"),

    # BROKEN — Bracknell Forest moved to Arcus BE (Salesforce):
    # publicaccess.bracknell-forest.gov.uk/s/register-view?c__r=Arcus_BE_Public_Register
    # planapp.bracknell-forest.gov.uk DNS is dead (decommissioned).
    # ("Bracknell Forest Council",
    #  "https://planapp.bracknell-forest.gov.uk/online-applications"),

    ("West Berkshire Council",
     "https://publicaccess.westberks.gov.uk/online-applications"),

    ("Windsor and Maidenhead Borough Council",
     "https://publicaccess.rbwm.gov.uk/online-applications"),

    ("Epsom and Ewell Borough Council",
     "https://eplanning.epsom-ewell.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # SOUTH WEST
    # -------------------------------------------------------------------------
    # NOTE: planning.plymouth.gov.uk redirects to Gloucester's Idox server.
    ("Gloucester City Council",
     "https://planning.plymouth.gov.uk/online-applications"),

    # BROKEN — planning.plymouth.gov.uk redirects to Gloucester; Plymouth needs research.
    # ("Plymouth City Council",
    #  "https://planning.plymouth.gov.uk/online-applications"),

    ("Exeter City Council",
     "https://publicaccess.exeter.gov.uk/online-applications"),

    ("Cornwall Council",
     "https://planning.cornwall.gov.uk/online-applications"),

    ("South Gloucestershire Council",
     "https://developments.southglos.gov.uk/online-applications"),

    ("North Somerset Council",
     "https://planning.n-somerset.gov.uk/online-applications"),

    # NOTE: publicaccess.cheltenham.gov.uk redirects to Ipswich's Idox server.
    ("Ipswich Borough Council",
     "https://publicaccess.cheltenham.gov.uk/online-applications"),

    # BROKEN — publicaccess.cheltenham.gov.uk redirects to Ipswich; Cheltenham needs research.
    # ("Cheltenham Borough Council",
    #  "https://publicaccess.cheltenham.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # EAST OF ENGLAND
    # -------------------------------------------------------------------------
    # Ipswich is now listed in SOUTH WEST section above (using Cheltenham's working URL)

    ("Peterborough City Council",
     "https://planpa.peterborough.gov.uk/online-applications"),

    ("Norwich City Council",
     "https://planning.norwich.gov.uk/online-applications"),

    ("Thurrock Council",
     "https://regs.thurrock.gov.uk/online-applications"),

    ("Bedford Borough Council",
     "https://publicaccess.bedford.gov.uk/online-applications"),

    ("Babergh District Council",
     "https://planning.baberghmidsuffolk.gov.uk/online-applications"),

    ("Tendring District Council",
     "https://idox.tendringdc.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # LONDON BOROUGHS — URLs verified from known working Idox installations
    # Note: some use non-standard subdomains (pa., pam., publicaccess2. etc)
    # Camden already covered by open data feed
    # -------------------------------------------------------------------------
    # BROKEN — Hackney moved off Idox; now uses planningapps.hackney.gov.uk (non-Idox system).
    # ("London Borough of Hackney",
    #  "https://planning.hackney.gov.uk/online-applications"),

    ("London Borough of Southwark",
     "https://planning.southwark.gov.uk/online-applications"),

    ("London Borough of Lambeth",
     "https://planning.lambeth.gov.uk/online-applications"),

    # NOTE: planning.lewisham.gov.uk redirects to Waltham Forest's Idox server.
    ("London Borough of Waltham Forest",
     "https://planning.lewisham.gov.uk/online-applications"),

    # BROKEN — planning.lewisham.gov.uk redirects to WF; Lewisham needs research.
    # ("London Borough of Lewisham",
    #  "https://planning.lewisham.gov.uk/online-applications"),

    # NOTE: development.towerhamlets.gov.uk redirects to Newham's Idox server.
    ("London Borough of Newham",
     "https://development.towerhamlets.gov.uk/online-applications"),

    # BROKEN — development.towerhamlets.gov.uk redirects to Newham; Tower Hamlets needs research.
    # ("London Borough of Tower Hamlets",
    #  "https://development.towerhamlets.gov.uk/online-applications"),

    # BROKEN — Redbridge uses Swift LG (planning.redbridge.gov.uk/swiftlg/apas), not Idox.
    # ("London Borough of Redbridge",
    #  "https://www.redbridge.gov.uk/online-applications"),

    # BROKEN — Havering uses OcellaWeb/Northgate (development.havering.gov.uk/OcellaWeb), not Idox.
    # ("London Borough of Havering",
    #  "https://development.havering.gov.uk/online-applications"),

    ("London Borough of Bexley",
     "https://pa.bexley.gov.uk/online-applications"),

    # NOTE: planning.royalgreenwich.gov.uk redirects to Richmond's Idox server.
    ("London Borough of Richmond upon Thames",
     "https://planning.royalgreenwich.gov.uk/online-applications"),

    # BROKEN — planning.royalgreenwich.gov.uk redirects to Richmond; Greenwich needs research.
    # ("London Borough of Greenwich",
    #  "https://planning.royalgreenwich.gov.uk/online-applications"),

    ("London Borough of Bromley",
     "https://searchapplications.bromley.gov.uk/online-applications"),

    ("London Borough of Croydon",
     "https://publicaccess3.croydon.gov.uk/online-applications"),

    ("London Borough of Sutton",
     "https://planningregister.sutton.gov.uk/online-applications"),

    # BROKEN — Merton uses Northgate (planning.merton.gov.uk/Northgate), not Idox.
    # ("London Borough of Merton",
    #  "https://www.merton.gov.uk/online-applications"),

    ("London Borough of Kingston upon Thames",
     "https://publicaccess.kingston.gov.uk/online-applications"),

    # Richmond now uses Greenwich's URL (see above); old www.richmond.gov.uk was broken.

    # BROKEN — Hounslow uses a non-Idox system (planning.hounslow.gov.uk/Planning_Index.aspx).
    # www.hounslow.gov.uk/online-applications redirects to Richmond's Idox server.
    # ("London Borough of Hounslow",
    #  "https://www.hounslow.gov.uk/online-applications"),

    ("London Borough of Ealing",
     "https://pam.ealing.gov.uk/online-applications"),

    # BROKEN — Hillingdon uses OcellaWeb/Northgate (planning.hillingdon.gov.uk/OcellaWeb), not Idox.
    # ("London Borough of Hillingdon",
    #  "https://www.hillingdon.gov.uk/online-applications"),

    # BROKEN — Harrow uses a custom system (planningsearch.harrow.gov.uk/planning/search-applications), not Idox.
    # ("London Borough of Harrow",
    #  "https://www.harrow.gov.uk/online-applications"),

    ("London Borough of Brent",
     "https://pa.brent.gov.uk/online-applications"),

    ("London Borough of Barnet",
     "https://publicaccess.barnet.gov.uk/online-applications"),

    ("London Borough of Enfield",
     "https://planningandbuildingcontrol.enfield.gov.uk/online-applications"),

    # BROKEN — Haringey uses a custom system (planningservices.haringey.gov.uk/portal/servlets), not Idox.
    # ("London Borough of Haringey",
    #  "https://www.haringey.gov.uk/online-applications"),

    # BROKEN — Islington uses Northgate (planning.islington.gov.uk/northgate/planningexplorer), not Idox.
    # ("London Borough of Islington",
    #  "https://www.islington.gov.uk/online-applications"),

    ("London Borough of Hammersmith and Fulham",
     "https://public-access.lbhf.gov.uk/online-applications"),

    ("City of Westminster",
     "https://idoxpa.westminster.gov.uk/online-applications"),

    ("City of London",
     "https://www.planning2.cityoflondon.gov.uk/online-applications"),

]

# ---------------------------------------------------------------------------
# SQL to insert all Idox councils into Supabase
# Run once in Supabase SQL editor before the first scrape
# ---------------------------------------------------------------------------
INSERT_SQL = """
INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
VALUES
  ('Manchester City Council','manchester-city-council','idox','england','https://pa.manchester.gov.uk/online-applications','pending',true),
  ('Salford City Council','salford-city-council','idox','england','https://publicaccess.salford.gov.uk/online-applications','pending',true),
  ('Stockport Metropolitan Borough Council','stockport-metropolitan-borough-council','idox','england','https://planning.stockport.gov.uk/online-applications','pending',true),
  ('Trafford Council','trafford-council','idox','england','https://www.trafford.gov.uk/online-applications','pending',true),
  ('Bolton Metropolitan Borough Council','bolton-metropolitan-borough-council','idox','england','https://www.bolton.gov.uk/idox/online-applications','pending',true),
  ('Bury Metropolitan Borough Council','bury-metropolitan-borough-council','idox','england','https://planning.bury.gov.uk/online-applications','pending',true),
  ('Oldham Metropolitan Borough Council','oldham-metropolitan-borough-council','idox','england','https://online.oldham.gov.uk/online-applications','pending',true),
  ('Rochdale Borough Council','rochdale-borough-council','idox','england','https://planning.rochdale.gov.uk/online-applications','pending',true),
  ('Tameside Metropolitan Borough Council','tameside-metropolitan-borough-council','idox','england','https://www.tameside.gov.uk/online-applications','pending',true),
  ('Sheffield City Council','sheffield-city-council','idox','england','https://planningapps.sheffield.gov.uk/online-applications','pending',true),
  ('Bradford Metropolitan District Council','bradford-metropolitan-district-council','idox','england','https://publicaccess.bradford.gov.uk/online-applications','pending',true),
  ('Calderdale Metropolitan Borough Council','calderdale-metropolitan-borough-council','idox','england','https://www.calderdale.gov.uk/online-applications','pending',true),
  ('Kirklees Council','kirklees-council','idox','england','https://www.kirklees.gov.uk/online-applications','pending',true),
  ('Liverpool City Council','liverpool-city-council','idox','england','https://planning.liverpool.gov.uk/online-applications','pending',true),
  ('Wirral Metropolitan Borough Council','wirral-metropolitan-borough-council','idox','england','https://www.wirral.gov.uk/online-applications','pending',true),
  ('Knowsley Metropolitan Borough Council','knowsley-metropolitan-borough-council','idox','england','https://www.knowsley.gov.uk/online-applications','pending',true),
  ('St. Helens Metropolitan Borough Council','st-helens-metropolitan-borough-council','idox','england','https://www.sthelens.gov.uk/online-applications','pending',true),
  ('Warrington Borough Council','warrington-borough-council','idox','england','https://planning.warrington.gov.uk/online-applications','pending',true),
  ('Cheshire West and Chester Council','cheshire-west-and-chester-council','idox','england','https://www.cheshirewestandchester.gov.uk/online-applications','pending',true),
  ('Cheshire East Council','cheshire-east-council','idox','england','https://planning.cheshireeast.gov.uk/online-applications','pending',true),
  ('Sefton Metropolitan Borough Council','sefton-metropolitan-borough-council','idox','england','https://pa.sefton.gov.uk/online-applications','pending',true),
  ('Halton Borough Council','halton-borough-council','idox','england','https://webapp.halton.gov.uk/PlanningApps4','pending',true),
  ('Coventry City Council','coventry-city-council','idox','england','https://planningapps.coventry.gov.uk/online-applications','pending',true),
  ('Wolverhampton City Council','wolverhampton-city-council','idox','england','https://www.wolverhampton.gov.uk/online-applications','pending',true),
  ('Walsall Metropolitan Borough Council','walsall-metropolitan-borough-council','idox','england','https://www.walsall.gov.uk/online-applications','pending',true),
  ('Sandwell Metropolitan Borough Council','sandwell-metropolitan-borough-council','idox','england','https://sandwell.gov.uk/online-applications','pending',true),
  ('Dudley Metropolitan Borough Council','dudley-metropolitan-borough-council','idox','england','https://www.dudley.gov.uk/online-applications','pending',true),
  ('Solihull Metropolitan Borough Council','solihull-metropolitan-borough-council','idox','england','https://eservices.solihull.gov.uk/online-applications','pending',true),
  ('Leicester City Council','leicester-city-council','idox','england','https://publicaccess.leicester.gov.uk/online-applications','pending',true),
  ('Derby City Council','derby-city-council','idox','england','https://eplanning.derby.gov.uk/online-applications','pending',true),
  ('Nottinghamshire County Council','nottinghamshire-county-council','idox','england','https://publicaccess.nottinghamshire.gov.uk/online-applications','pending',true),
  ('Newcastle City Council','newcastle-city-council','idox','england','https://publicaccess.newcastle.gov.uk/pa/pa.nsf/SearchSimple?OpenForm','pending',true),
  ('Sunderland City Council','sunderland-city-council','idox','england','https://www.sunderland.gov.uk/online-applications','pending',true),
  ('Gateshead Council','gateshead-council','idox','england','https://planning.gateshead.gov.uk/online-applications','pending',true),
  ('South Tyneside Metropolitan Borough Council','south-tyneside-metropolitan-borough-council','idox','england','https://www.southtyneside.gov.uk/online-applications','pending',true),
  ('North Tyneside Council','north-tyneside-council','idox','england','https://www.northtyneside.gov.uk/online-applications','pending',true),
  ('Durham County Council','durham-county-council','idox','england','https://publicaccess.durham.gov.uk/online-applications','pending',true),
  ('Middlesbrough Council','middlesbrough-council','idox','england','https://planning.middlesbrough.gov.uk/online-applications','pending',true),
  ('Stockton-on-Tees Borough Council','stockton-on-tees-borough-council','idox','england','https://www.stockton.gov.uk/online-applications','pending',true),
  ('Brighton and Hove City Council','brighton-and-hove-city-council','idox','england','https://planningapps.brighton-hove.gov.uk/online-applications','pending',true),
  ('Southampton City Council','southampton-city-council','idox','england','https://www.southampton.gov.uk/online-applications','pending',true),
  ('Portsmouth City Council','portsmouth-city-council','idox','england','https://www.portsmouth.gov.uk/online-applications','pending',true),
  ('Reading Borough Council','reading-borough-council','idox','england','https://planning.reading.gov.uk/online-applications','pending',true),
  ('Milton Keynes City Council','milton-keynes-city-council','idox','england','https://www.milton-keynes.gov.uk/online-applications','pending',true),
  ('Oxford City Council','oxford-city-council','idox','england','https://www.oxford.gov.uk/online-applications','pending',true),
  ('Medway Council','medway-council','idox','england','https://publicaccess.medway.gov.uk/online-applications','pending',true),
  ('Plymouth City Council','plymouth-city-council','idox','england','https://planning.plymouth.gov.uk/online-applications','pending',true),
  ('Exeter City Council','exeter-city-council','idox','england','https://publicaccess.exeter.gov.uk/online-applications','pending',true),
  ('Cheltenham Borough Council','cheltenham-borough-council','idox','england','https://publicaccess.cheltenham.gov.uk/online-applications','pending',true),
  ('Gloucester City Council','gloucester-city-council','idox','england','https://www.gloucester.gov.uk/online-applications','pending',true),
  ('Ipswich Borough Council','ipswich-borough-council','idox','england','https://www.ipswich.gov.uk/online-applications','pending',true),
  ('Peterborough City Council','peterborough-city-council','idox','england','https://www.peterborough.gov.uk/online-applications','pending',true),
  ('Norwich City Council','norwich-city-council','idox','england','https://planning.norwich.gov.uk/online-applications','pending',true),
  ('London Borough of Hackney','london-borough-of-hackney','idox','england','https://planning.hackney.gov.uk/online-applications','pending',true),
  ('London Borough of Southwark','london-borough-of-southwark','idox','england','https://planning.southwark.gov.uk/online-applications','pending',true),
  ('London Borough of Lambeth','london-borough-of-lambeth','idox','england','https://planning.lambeth.gov.uk/online-applications','pending',true),
  ('London Borough of Lewisham','london-borough-of-lewisham','idox','england','https://planning.lewisham.gov.uk/online-applications','pending',true),
  ('London Borough of Tower Hamlets','london-borough-of-tower-hamlets','idox','england','https://development.towerhamlets.gov.uk/online-applications','pending',true),
  ('London Borough of Newham','london-borough-of-newham','idox','england','https://www.newham.gov.uk/online-applications','pending',true),
  ('London Borough of Waltham Forest','london-borough-of-waltham-forest','idox','england','https://www.walthamforest.gov.uk/online-applications','pending',true),
  ('London Borough of Redbridge','london-borough-of-redbridge','idox','england','https://www.redbridge.gov.uk/online-applications','pending',true),
  ('London Borough of Barking and Dagenham','london-borough-of-barking-and-dagenham','idox','england','https://www.lbbd.gov.uk/online-applications','pending',true),
  ('London Borough of Havering','london-borough-of-havering','idox','england','https://development.havering.gov.uk/online-applications','pending',true),
  ('London Borough of Bexley','london-borough-of-bexley','idox','england','https://pa.bexley.gov.uk/online-applications','pending',true),
  ('London Borough of Greenwich','london-borough-of-greenwich','idox','england','https://planning.royalgreenwich.gov.uk/online-applications','pending',true),
  ('London Borough of Bromley','london-borough-of-bromley','idox','england','https://www.bromley.gov.uk/online-applications','pending',true),
  ('London Borough of Croydon','london-borough-of-croydon','idox','england','https://www.croydon.gov.uk/online-applications','pending',true),
  ('London Borough of Sutton','london-borough-of-sutton','idox','england','https://www.sutton.gov.uk/online-applications','pending',true),
  ('London Borough of Merton','london-borough-of-merton','idox','england','https://www.merton.gov.uk/online-applications','pending',true),
  ('London Borough of Kingston upon Thames','london-borough-of-kingston-upon-thames','idox','england','https://www.kingston.gov.uk/online-applications','pending',true),
  ('London Borough of Richmond upon Thames','london-borough-of-richmond-upon-thames','idox','england','https://www.richmond.gov.uk/online-applications','pending',true),
  ('London Borough of Hounslow','london-borough-of-hounslow','idox','england','https://www.hounslow.gov.uk/online-applications','pending',true),
  ('London Borough of Ealing','london-borough-of-ealing','idox','england','https://www.ealing.gov.uk/online-applications','pending',true),
  ('London Borough of Hillingdon','london-borough-of-hillingdon','idox','england','https://www.hillingdon.gov.uk/online-applications','pending',true),
  ('London Borough of Harrow','london-borough-of-harrow','idox','england','https://www.harrow.gov.uk/online-applications','pending',true),
  ('London Borough of Brent','london-borough-of-brent','idox','england','https://www.brent.gov.uk/online-applications','pending',true),
  ('London Borough of Barnet','london-borough-of-barnet','idox','england','https://publicaccess.barnet.gov.uk/online-applications','pending',true),
  ('London Borough of Enfield','london-borough-of-enfield','idox','england','https://planningandbuildingcontrol.enfield.gov.uk/online-applications','pending',true),
  ('London Borough of Haringey','london-borough-of-haringey','idox','england','https://www.haringey.gov.uk/online-applications','pending',true),
  ('London Borough of Islington','london-borough-of-islington','idox','england','https://www.islington.gov.uk/online-applications','pending',true)
ON CONFLICT (name) DO UPDATE SET
  system = 'idox',
  active = true,
  portal_url = EXCLUDED.portal_url;
"""

if __name__ == "__main__":
    print(INSERT_SQL)

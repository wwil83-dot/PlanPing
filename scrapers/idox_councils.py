"""
PlanFind — Idox portal council list.

Format: (council_name_as_in_supabase_db, idox_base_url)

IMPORTANT: The base_url should end at /online-applications (no trailing slash).
The scraper appends /search.do and /pagedSearchResults.do automatically.

Add new councils here after confirming:
  1. The council uses Idox (not Northgate / OcellaAccess / Uniform)
  2. The base URL resolves to a working Idox search page
  3. The council name matches the councils table in Supabase

To add a new council to the database, run this in Supabase SQL editor:
  INSERT INTO councils (name, slug, system, region, portal_url, coverage_source, active)
  VALUES ('Council Name', 'council-name', 'idox', 'england', 'https://...', 'pending', true)
  ON CONFLICT (name) DO UPDATE SET system = 'idox', active = true;
"""

IDOX_COUNCILS = [

    # -------------------------------------------------------------------------
    # GREATER MANCHESTER — all 9 boroughs (excl. Wigan, already on open data)
    # -------------------------------------------------------------------------
    ("Manchester City Council",
     "https://pa.manchester.gov.uk/online-applications"),

    ("Salford City Council",
     "https://publicaccess.salford.gov.uk/online-applications"),

    ("Stockport Metropolitan Borough Council",
     "https://planning.stockport.gov.uk/online-applications"),

    ("Trafford Council",
     "https://www.trafford.gov.uk/online-applications"),

    ("Bolton Metropolitan Borough Council",
     "https://www.bolton.gov.uk/idox/online-applications"),

    ("Bury Metropolitan Borough Council",
     "https://planning.bury.gov.uk/online-applications"),

    ("Oldham Metropolitan Borough Council",
     "https://planningpa.oldham.gov.uk/online-applications"),

    ("Rochdale Borough Council",
     "https://publicaccess.rochdale.gov.uk/online-applications"),

    ("Tameside Metropolitan Borough Council",
     "https://www.tameside.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # YORKSHIRE
    # -------------------------------------------------------------------------
    ("Sheffield City Council",
     "https://planningapps.sheffield.gov.uk/online-applications"),

    ("Bradford Metropolitan District Council",
     "https://planning.bradford.gov.uk/online-applications"),

    ("Calderdale Metropolitan Borough Council",
     "https://www.calderdale.gov.uk/online-applications"),

    ("Kirklees Council",
     "https://www.kirklees.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # NORTH WEST (excl. Greater Manchester)
    # -------------------------------------------------------------------------
    ("Liverpool City Council",
     "https://planning.liverpool.gov.uk/online-applications"),

    ("Wirral Metropolitan Borough Council",
     "https://www.wirral.gov.uk/online-applications"),

    ("Knowsley Metropolitan Borough Council",
     "https://www.knowsley.gov.uk/online-applications"),

    ("St. Helens Metropolitan Borough Council",
     "https://www.sthelens.gov.uk/online-applications"),

    ("Warrington Borough Council",
     "https://publicaccess.warrington.gov.uk/online-applications"),

    ("Cheshire West and Chester Council",
     "https://pa.cheshirewestandchester.gov.uk/online-applications"),

    ("Cheshire East Council",
     "https://planning.cheshireeast.gov.uk/online-applications"),

    ("Sefton Metropolitan Borough Council",
     "https://pa.sefton.gov.uk/online-applications"),

    ("Halton Borough Council",
     "https://webapp.halton.gov.uk/PlanningApps4"),

    # -------------------------------------------------------------------------
    # WEST MIDLANDS
    # -------------------------------------------------------------------------
    # Coventry moved to planandregulatory.coventry.gov.uk (not Idox) — removed

    ("Wolverhampton City Council",
     "https://www.wolverhampton.gov.uk/planning/search-planning-applications"),

    ("Walsall Metropolitan Borough Council",
     "https://www.walsall.gov.uk/online-applications"),

    ("Sandwell Metropolitan Borough Council",
     "https://sandwell.gov.uk/online-applications"),

    ("Dudley Metropolitan Borough Council",
     "https://www.dudley.gov.uk/online-applications"),

    ("Solihull Metropolitan Borough Council",
     "https://eservices.solihull.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # EAST MIDLANDS
    # -------------------------------------------------------------------------
    ("Leicester City Council",
     "https://publicaccess.leicester.gov.uk/online-applications"),

    ("Derby City Council",
     "https://eplanning.derby.gov.uk/online-applications"),

    ("Nottinghamshire County Council",
     "https://publicaccess.nottinghamshire.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # NORTH EAST
    # -------------------------------------------------------------------------
    ("Newcastle City Council",
     "https://publicaccess.newcastle.gov.uk/online-applications"),

    ("Sunderland City Council",
     "https://publicaccess.sunderland.gov.uk/online-applications"),

    ("Gateshead Council",
     "https://public.gateshead.gov.uk/online-applications"),

    ("South Tyneside Metropolitan Borough Council",
     "https://www.southtyneside.gov.uk/online-applications"),

    ("North Tyneside Council",
     "https://www.northtyneside.gov.uk/online-applications"),

    ("Durham County Council",
     "https://publicaccess.durham.gov.uk/online-applications"),

    ("Middlesbrough Council",
     "https://www.middlesbrough.gov.uk/online-applications"),

    ("Stockton-on-Tees Borough Council",
     "https://www.stockton.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # SOUTH EAST
    # -------------------------------------------------------------------------
    ("Brighton and Hove City Council",
     "https://planningapps.brighton-hove.gov.uk/online-applications"),

    ("Southampton City Council",
     "https://www.southampton.gov.uk/online-applications"),

    ("Portsmouth City Council",
     "https://www.portsmouth.gov.uk/online-applications"),

    ("Reading Borough Council",
     "https://planning.reading.gov.uk/online-applications"),

    ("Milton Keynes City Council",
     "https://www.milton-keynes.gov.uk/online-applications"),

    ("Oxford City Council",
     "https://www.oxford.gov.uk/online-applications"),

    ("Medway Council",
     "https://publicaccess.medway.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # SOUTH WEST
    # -------------------------------------------------------------------------
    ("Plymouth City Council",
     "https://planning.plymouth.gov.uk/online-applications"),

    ("Exeter City Council",
     "https://publicaccess.exeter.gov.uk/online-applications"),

    ("Cheltenham Borough Council",
     "https://publicaccess.cheltenham.gov.uk/online-applications"),

    ("Gloucester City Council",
     "https://www.gloucester.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # EAST OF ENGLAND
    # -------------------------------------------------------------------------
    ("Ipswich Borough Council",
     "https://www.ipswich.gov.uk/online-applications"),

    ("Peterborough City Council",
     "https://www.peterborough.gov.uk/online-applications"),

    ("Norwich City Council",
     "https://planning.norwich.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # LONDON BOROUGHS — URLs verified from known working Idox installations
    # Note: some use non-standard subdomains (pa., pam., publicaccess2. etc)
    # Camden already covered by open data feed
    # -------------------------------------------------------------------------
    ("London Borough of Hackney",
     "https://planning.hackney.gov.uk/online-applications"),

    ("London Borough of Southwark",
     "https://planbuild.southwark.gov.uk:8190/online-applications"),

    ("London Borough of Lambeth",
     "https://planning.lambeth.gov.uk/online-applications"),

    ("London Borough of Lewisham",
     "https://planning.lewisham.gov.uk/online-applications"),

    ("London Borough of Tower Hamlets",
     "https://development.towerhamlets.gov.uk/online-applications"),

    ("London Borough of Newham",
     "https://pa.newham.gov.uk/online-applications"),

    ("London Borough of Waltham Forest",
     "https://www.walthamforest.gov.uk/online-applications"),

    ("London Borough of Redbridge",
     "https://www.redbridge.gov.uk/online-applications"),

    ("London Borough of Barking and Dagenham",
     "https://pa.lbbd.gov.uk/online-applications"),

    ("London Borough of Havering",
     "https://development.havering.gov.uk/online-applications"),

    ("London Borough of Bexley",
     "https://pa.bexley.gov.uk/online-applications"),

    ("London Borough of Greenwich",
     "https://planning.royalgreenwich.gov.uk/online-applications"),

    ("London Borough of Bromley",
     "https://searchapplications.bromley.gov.uk/onlineapplications"),

    ("London Borough of Croydon",
     "https://publicaccess2.croydon.gov.uk/online-applications"),

    ("London Borough of Sutton",
     "https://www.sutton.gov.uk/online-applications"),

    ("London Borough of Merton",
     "https://www.merton.gov.uk/online-applications"),

    ("London Borough of Kingston upon Thames",
     "https://www.kingston.gov.uk/online-applications"),

    ("London Borough of Richmond upon Thames",
     "https://www.richmond.gov.uk/online-applications"),

    ("London Borough of Hounslow",
     "https://www.hounslow.gov.uk/online-applications"),

    ("London Borough of Ealing",
     "https://pam.ealing.gov.uk/online-applications"),

    ("London Borough of Hillingdon",
     "https://www.hillingdon.gov.uk/online-applications"),

    ("London Borough of Harrow",
     "https://www.harrow.gov.uk/online-applications"),

    ("London Borough of Brent",
     "https://pa.brent.gov.uk/online-applications"),

    ("London Borough of Barnet",
     "https://publicaccess.barnet.gov.uk/online-applications"),

    ("London Borough of Enfield",
     "https://planningandbuildingcontrol.enfield.gov.uk/online-applications"),

    ("London Borough of Haringey",
     "https://www.haringey.gov.uk/online-applications"),

    ("London Borough of Islington",
     "https://www.islington.gov.uk/online-applications"),

    ("London Borough of Hammersmith and Fulham",
     "https://public-access.lbhf.gov.uk/online-applications"),

    ("City of Westminster",
     "https://idoxpa.westminster.gov.uk/online-applications"),

    ("City of London Corporation",
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
  ('Newcastle City Council','newcastle-city-council','idox','england','https://publicaccess.newcastle.gov.uk/online-applications','pending',true),
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

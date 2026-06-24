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
    "Babergh District Council":                  9,
    "Bedford Borough Council":                   12,
    "Blackpool Council":                         15,
    "Brighton and Hove City Council":            20,
    "Cheshire West and Chester Council":         27,
    "City of London":                            28,   # note: NOT "Corporation" in DB
    "Gloucester City Council":                   37,
    "Leeds City Council":                        48,
    "Nottingham City Council":                   57,
    "Plymouth City Council":                     59,
    "Wolverhampton City Council":                79,
    "Canterbury City Council":                   82,
    "Stockport Metropolitan Borough Council":    167,
    "Bolton Metropolitan Borough Council":       169,
    "Rochdale Borough Council":                  172,
    "Tameside Metropolitan Borough Council":     173,
    "Bradford Metropolitan District Council":    175,
    "Knowsley Metropolitan Borough Council":     180,
    "Sefton Metropolitan Borough Council":       185,
    "Halton Borough Council":                    186,
    "North Tyneside Council":                    200,
    "Durham County Council":                     201,
    "Gateshead Council":                         198,
    "Portsmouth City Council":                   206,
    "Cheltenham Borough Council":                213,
    "Ipswich Borough Council":                   215,
    "Solihull Metropolitan Borough Council":     192,
    "London Borough of Tower Hamlets":           222,
    "London Borough of Newham":                  223,
    "London Borough of Waltham Forest":          224,
    "London Borough of Richmond upon Thames":    235,
    "London Borough of Brent":                   240,
    "Dacorum Borough Council":                   30,
    "Hertsmere Borough Council":                 43,
    "Wakefield Metropolitan District Council":   245,
    "Doncaster Metropolitan Borough Council":    246,
    "Darlington Borough Council":                247,
    "Northumberland County Council":             248,
    "Rutland County Council":                    249,
    "Stoke-on-Trent City Council":               250,
    "Isle of Wight Council":                     251,
    "Gravesham Borough Council":                 252,
    "Maidstone Borough Council":                 253,
    "Thanet District Council":                   254,
    "Mid Sussex District Council":               255,
    "Adur District Council":                     256,
    "South Downs National Park Authority":       257,
    "West Berkshire Council":                    258,
    "Windsor and Maidenhead Borough Council":    259,
    "Epsom and Ewell Borough Council":           260,
    "Cornwall Council":                          261,
    "South Gloucestershire Council":             262,
    "North Somerset Council":                    263,
    "Thurrock Council":                          264,
    "Tendring District Council":                 265,
    "London Borough of Hammersmith and Fulham":  266,
    "City of Westminster":                       267,
    # --- Surrey/Kent/Sussex/Essex/Herts/Cambs districts seeded Jun 2026 ---
    "Guildford Borough Council":                 268,
    "Surrey Heath Borough Council":              269,
    "Spelthorne Borough Council":                270,
    "Sevenoaks District Council":                271,
    "Dover District Council":                    272,
    "Tonbridge and Malling Borough Council":     273,
    "Tunbridge Wells Borough Council":           274,
    "Lewes District Council":                    275,
    "Rother District Council":                   276,
    "Wealden District Council":                  277,
    "Chichester District Council":               278,
    "Crawley Borough Council":                   279,
    "Horsham District Council":                  280,
    "Basildon Borough Council":                  281,
    "Braintree District Council":                282,
    "Castle Point Borough Council":              283,
    "Chelmsford City Council":                   284,
    "Harlow District Council":                   285,
    "Maldon District Council":                   286,
    "Southend-on-Sea City Council":              287,
    "Uttlesford District Council":               288,
    "East Hertfordshire District Council":       289,
    "Stevenage Borough Council":                 290,
    "Three Rivers District Council":             291,
    "Welwyn Hatfield Borough Council":           292,
    "Huntingdonshire District Council":          293,
    "Havant Borough Council":                    294,
    "East Hampshire District Council":           295,
    # --- Hampshire/Oxon/Norfolk/Cambs seeded Jun 2026 ---
    "Gosport Borough Council":                   296,
    "Hart District Council":                     297,
    "Basingstoke and Deane Borough Council":     298,
    "Rushmoor Borough Council":                  299,
    "New Forest District Council":               300,
    "Eastleigh Borough Council":                 301,
    "West Oxfordshire District Council":         302,
    "Breckland District Council":                303,
    "Fenland District Council":                  304,
    # --- Lancashire/Cambs/Norfolk/Derbys seeded Jun 2026 ---
    "Great Yarmouth Borough Council":            305,   # Northgate - commented out
    "East Cambridgeshire District Council":      306,
    "South Cambridgeshire District Council":     307,
    "Lancaster City Council":                    308,
    "Preston City Council":                      309,
    "Burnley Borough Council":                   310,
    "South Ribble Borough Council":              311,
    "Pendle Borough Council":                    312,
    "Chorley Borough Council":                   313,
    "Wyre Borough Council":                      314,
    "Rossendale Borough Council":                315,
    "West Lancashire Borough Council":           316,
    "Chesterfield Borough Council":              317,
    # --- Wales seeded Jun 2026 ---
    "Cardiff Council":                           26,
    "Newport City Council":                      319,
    "Neath Port Talbot County Borough Council":  320,
    "Denbighshire County Council":               321,
    "Carmarthenshire County Council":            322,
    "Powys County Council":                      323,
    "Eryri National Park Authority":             324,
    "Caerphilly County Borough Council":         325,
    "Torfaen County Borough Council":            326,
    "Monmouthshire County Council":              327,
    # --- Scotland seeded Jun 2026 ---
    "City of Edinburgh Council":                 328,
    "Dundee City Council":                       329,
    "Glasgow City Council":                      330,
    "Aberdeen City Council":                     1,    # pre-existing in DB
    "Highland Council":                          332,
    "Fife Council":                              333,
    "East Lothian Council":                      334,
    "Stirling Council":                          335,
    "South Lanarkshire Council":                 336,
    "West Lothian Council":                      337,
    "East Dunbartonshire Council":               338,
    "South Ayrshire Council":                    339,
    "Angus Council":                             340,
    "Moray Council":                             341,
    "Clackmannanshire Council":                  342,
    "Inverclyde Council":                        343,
    "Argyll and Bute Council":                   344,
    "Comhairle nan Eilean Siar":                 345,
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

    # BROKEN — Doncaster is blocked by Cloudflare ("Just a moment..." challenge).
    # ("Doncaster Metropolitan Borough Council",
    #  "https://planning.doncaster.gov.uk/online-applications"),

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

    # --- SURREY districts ---
    ("Guildford Borough Council",
     "https://publicaccess.guildford.gov.uk/online-applications"),

    ("Surrey Heath Borough Council",
     "https://publicaccess.surreyheath.gov.uk/online-applications"),

    ("Spelthorne Borough Council",
     "https://publicaccess.spelthorne.gov.uk/online-applications"),

    # NOT Idox — skip: Waverley (planning360 system), Runnymede (Northgate),
    # Elmbridge (emaps system), Woking/Tandridge/Mole Valley/Reigate (DNS not resolved)

    # --- KENT districts ---
    ("Sevenoaks District Council",
     "https://pa.sevenoaks.gov.uk/online-applications"),

    ("Dover District Council",
     "https://publicaccess.dover.gov.uk/online-applications"),

    ("Tonbridge and Malling Borough Council",
     "https://publicaccess.tmbc.gov.uk/online-applications"),

    # NOTE: twbcpa.midkent.gov.uk is Tunbridge Wells' partition of the shared midkent server.
    ("Tunbridge Wells Borough Council",
     "https://twbcpa.midkent.gov.uk/online-applications"),

    # --- EAST SUSSEX districts ---
    # NOTE: planningpa.lewes-eastbourne.gov.uk is a shared server for Lewes and Eastbourne.
    ("Lewes District Council",
     "https://planningpa.lewes-eastbourne.gov.uk/online-applications"),

    ("Rother District Council",
     "https://planweb01.rother.gov.uk/online-applications"),

    ("Wealden District Council",
     "https://planning.wealden.gov.uk/online-applications"),

    # --- WEST SUSSEX districts ---
    ("Chichester District Council",
     "https://publicaccess.chichester.gov.uk/online-applications"),

    # BROKEN — Crawley uses bespoke ASP.NET system at planningregister.crawley.gov.uk
    # (URL pattern: /Disclaimer?returnUrl=/Planning/Display/CR/...) — not Idox.
    # ("Crawley Borough Council",
    #  "https://planningregister.crawley.gov.uk/online-applications"),

    ("Horsham District Council",
     "https://public-access.horsham.gov.uk/public-access"),

    # -------------------------------------------------------------------------
    # HAMPSHIRE — additional Idox portals found during South East expansion
    # -------------------------------------------------------------------------
    ("Havant Borough Council",
     "https://planningpublicaccess.havant.gov.uk/online-applications"),

    # BROKEN — planningpublicaccess.easthants.gov.uk DNS DEAD.
    # New portal at easthants.gov.uk requires account login as of March 2026.
    # ("East Hampshire District Council",
    #  "https://planningpublicaccess.easthants.gov.uk/online-applications"),

    # --- ESSEX districts ---
    ("Basildon Borough Council",
     "https://planning.basildon.gov.uk/online-applications"),

    ("Braintree District Council",
     "https://publicaccess.braintree.gov.uk/online-applications"),

    ("Castle Point Borough Council",
     "https://publicaccess.castlepoint.gov.uk/online-applications"),

    ("Chelmsford City Council",
     "https://publicaccess.chelmsford.gov.uk/online-applications"),

    ("Harlow District Council",
     "https://planningonline.harlow.gov.uk/online-applications"),

    ("Maldon District Council",
     "https://publicaccess.maldon.gov.uk/online-applications"),

    ("Southend-on-Sea City Council",
     "https://publicaccess.southend.gov.uk/online-applications"),

    ("Uttlesford District Council",
     "https://publicaccess.uttlesford.gov.uk/online-applications"),

    # --- HERTFORDSHIRE districts ---
    # BROKEN — Dacorum's server (planning.dacorum.gov.uk/publicaccess) gives
    # ERR_EMPTY_RESPONSE consistently — accepts TCP but sends nothing back.
    # URL is correct but server appears to block cloud provider IP ranges.
    # ("Dacorum Borough Council",
    #  "https://planning.dacorum.gov.uk/publicaccess"),

    ("East Hertfordshire District Council",
     "https://publicaccess.eastherts.gov.uk/online-applications"),

    ("Hertsmere Borough Council",
     "https://www6.hertsmere.gov.uk/online-applications"),

    ("Stevenage Borough Council",
     "https://publicaccess.stevenage.gov.uk/online-applications"),

    ("Three Rivers District Council",
     "https://www3.threerivers.gov.uk/online-applications"),

    # NOTE: planning.welhat.gov.uk uses /publicaccess/ path not /online-applications/
    ("Welwyn Hatfield Borough Council",
     "https://planning.welhat.gov.uk/publicaccess"),

    # --- CAMBRIDGESHIRE ---
    ("Huntingdonshire District Council",
     "https://publicaccess.huntingdonshire.gov.uk/online-applications"),

    # BROKEN — Fenland gives ERR_HTTP2_PROTOCOL_ERROR (server-side HTTP/2 config issue).
    # ("Fenland District Council",
    #  "https://publicaccess.fenland.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # HAMPSHIRE districts (all confirmed Idox via 403 bot-block test)
    # Note: Winchester (ASPX), Fareham (bespoke ASPX), Test Valley (unknown) skip
    # Hampshire reorganises into 4 unitaries ~2027 but districts still process apps now
    # -------------------------------------------------------------------------
    ("Gosport Borough Council",
     "https://publicaccess.gosport.gov.uk/online-applications"),

    ("Hart District Council",
     "https://publicaccess.hart.gov.uk/online-applications"),

    ("Basingstoke and Deane Borough Council",
     "https://publicaccess.basingstoke.gov.uk/online-applications"),

    ("Rushmoor Borough Council",
     "https://publicaccess.rushmoor.gov.uk/online-applications"),

    ("New Forest District Council",
     "https://planning.newforest.gov.uk/online-applications"),

    # BROKEN — Eastleigh's Idox portal requires login (not public access).
    # ("Eastleigh Borough Council",
    #  "https://planning.eastleigh.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # OXFORDSHIRE
    # -------------------------------------------------------------------------
    ("West Oxfordshire District Council",
     "https://publicaccess.westoxon.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # NORFOLK
    # -------------------------------------------------------------------------
    ("Breckland District Council",
     "https://planning.breckland.gov.uk/online-applications"),

    # BROKEN — Great Yarmouth uses Northgate OcellaWeb system:
    # planning.great-yarmouth.gov.uk/OcellaWeb/planningSearch — NOT Idox.
    # ("Great Yarmouth Borough Council",
    #  "https://planning.great-yarmouth.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # CAMBRIDGESHIRE
    # -------------------------------------------------------------------------
    ("East Cambridgeshire District Council",
     "https://pa.eastcambs.gov.uk/online-applications"),

    ("South Cambridgeshire District Council",
     "https://planning.scambs.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # LANCASHIRE
    # -------------------------------------------------------------------------
    # BROKEN — Lancaster, Preston, Burnley all timeout at 60s (WAF/cloud-IP blocking)
    # Consistent across 30s/45s/60s runs. Not worth the 3-minute drain per run.
    # ("Lancaster City Council",
    #  "https://planning.lancaster.gov.uk/online-applications"),
    # ("Preston City Council",
    #  "https://publicaccess.preston.gov.uk/online-applications"),
    # ("Burnley Borough Council",
    #  "https://publicaccess.burnley.gov.uk/online-applications"),

    ("South Ribble Borough Council",
     "https://publicaccess.southribble.gov.uk/online-applications"),

    ("Pendle Borough Council",
     "https://publicaccess.pendle.gov.uk/online-applications"),

    ("Chorley Borough Council",
     "https://planning.chorley.gov.uk/online-applications"),

    ("Wyre Borough Council",
     "https://publicaccess.wyre.gov.uk/online-applications"),

    ("Rossendale Borough Council",
     "https://publicaccess.rossendale.gov.uk/online-applications"),

    ("West Lancashire Borough Council",
     "https://publicaccess.westlancs.gov.uk/online-applications"),

    # -------------------------------------------------------------------------
    # WALES
    # Note: Welsh councils not yet tried: Swansea, Bridgend, RCT, Wrexham,
    # Conwy, Flintshire, Gwynedd, Anglesey, Pembrokeshire, Ceredigion,
    # Blaenau Gwent, Merthyr Tydfil, Vale of Glamorgan — DNS not found yet
    # -------------------------------------------------------------------------
    ("Cardiff Council",
     "https://planning.cardiff.gov.uk/online-applications"),

    ("Newport City Council",
     "https://planning.newport.gov.uk/online-applications"),

    ("Neath Port Talbot County Borough Council",
     "https://planning.npt.gov.uk/online-applications"),

    ("Denbighshire County Council",
     "https://planning.denbighshire.gov.uk/online-applications"),

    ("Carmarthenshire County Council",
     "https://planning.carmarthenshire.gov.uk/online-applications"),

    ("Powys County Council",
     "https://pa.powys.gov.uk/online-applications"),

    # NOT IDOX — Eryri uses Agile Applications: planning.agileapplications.co.uk/snowdonia
    # ("Eryri National Park Authority",
    #  "https://pa.eryri.llyw.cymru/publicaccess"),

    # NOTE: Caerphilly gives 404 on /online-applications in browser - try /publicaccess/
    ("Caerphilly County Borough Council",
     "https://planningonline.caerphilly.gov.uk/publicaccess"),

    ("Torfaen County Borough Council",
     "https://publicaccess.torfaen.gov.uk/online-applications"),

    ("Monmouthshire County Council",
     "https://publicaccess.monmouthshire.gov.uk/online-applications"),

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

    # -------------------------------------------------------------------------
    # SCOTLAND (18 confirmed Idox portals)
    # Note: Edinburgh uses /idoxpa-web path (not /online-applications)
    # Note: Falkirk, Midlothian, Renfrewshire, Perth & Kinross, North Lanarkshire,
    #       East/North Ayrshire, Scottish Borders not yet found - DNS fails
    # -------------------------------------------------------------------------
    ("City of Edinburgh Council",
     "https://citydev-portal.edinburgh.gov.uk/idoxpa-web"),

    ("Dundee City Council",
     "https://portal.dundeecity.gov.uk/online-applications"),

    ("Glasgow City Council",
     "https://publicaccess.glasgow.gov.uk/online-applications"),

    ("Aberdeen City Council",
     "https://publicaccess.aberdeencity.gov.uk/online-applications"),

    ("Highland Council",
     "https://wam.highland.gov.uk/online-applications"),

    ("Fife Council",
     "https://planning.fife.gov.uk/idoxpa-web"),

    ("East Lothian Council",
     "https://pa.eastlothian.gov.uk/online-applications"),

    ("Stirling Council",
     "https://planning.stirling.gov.uk/online-applications"),

    ("South Lanarkshire Council",
     "https://publicaccess.southlanarkshire.gov.uk/online-applications"),

    ("West Lothian Council",
     "https://planning.westlothian.gov.uk/online-applications"),

    ("East Dunbartonshire Council",
     "https://planning.eastdunbarton.gov.uk/online-applications"),

    ("South Ayrshire Council",
     "https://publicaccess.south-ayrshire.gov.uk/online-applications"),

    ("Angus Council",
     "https://planning.angus.gov.uk/online-applications"),

    ("Moray Council",
     "https://publicaccess.moray.gov.uk/publicaccess"),

    ("Clackmannanshire Council",
     "https://publicaccess.clacks.gov.uk/publicaccess"),

    ("Inverclyde Council",
     "https://planning.inverclyde.gov.uk/publicaccess"),

    ("Argyll and Bute Council",
     "https://publicaccess.argyll-bute.gov.uk/online-applications"),

    ("Comhairle nan Eilean Siar",
     "https://planning.cne-siar.gov.uk/publicaccess"),

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

-- PlanPing — database schema
-- Run against Postgres (Supabase / Neon / local)

-- ─────────────────────────────────────────────
-- Councils
-- ─────────────────────────────────────────────
CREATE TABLE councils (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    slug            TEXT NOT NULL UNIQUE,          -- url-safe e.g. 'bristol-city-council'
    portal_url      TEXT,                          -- council's own planning portal
    system          TEXT DEFAULT 'unknown',        -- idox | northgate | other | unknown
    region          TEXT DEFAULT 'england',        -- england | scotland | wales | northern_ireland
    coverage_source TEXT DEFAULT 'pending',        -- gov_api | idox_scraper | northgate_scraper | manual_link | none
    lat             DOUBLE PRECISION,              -- centre of council area
    lng             DOUBLE PRECISION,
    active          BOOLEAN DEFAULT TRUE,
    last_scraped_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_councils_slug   ON councils(slug);
CREATE INDEX idx_councils_system ON councils(system);
CREATE INDEX idx_councils_source ON councils(coverage_source);

-- ─────────────────────────────────────────────
-- Planning applications
-- ─────────────────────────────────────────────
CREATE TABLE planning_applications (
    id              SERIAL PRIMARY KEY,
    council_id      INTEGER REFERENCES councils(id) ON DELETE CASCADE,
    reference       TEXT NOT NULL,                 -- council's own reference e.g. '23/01234/FUL'
    address         TEXT,
    postcode        TEXT,
    lat             DOUBLE PRECISION,
    lng             DOUBLE PRECISION,
    description     TEXT,
    application_type TEXT,                         -- e.g. 'Full', 'Householder', 'Listed Building'
    status          TEXT DEFAULT 'pending',        -- pending | approved | refused | withdrawn
    submitted_date  DATE,
    decision_date   DATE,
    decision        TEXT,
    applicant_name  TEXT,
    agent_name      TEXT,
    council_url     TEXT,                          -- direct link to this application on council portal
    source          TEXT DEFAULT 'scraper',        -- gov_api | scraper
    raw_data        JSONB,                         -- store original scraped data
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(council_id, reference)
);

CREATE INDEX idx_apps_council     ON planning_applications(council_id);
CREATE INDEX idx_apps_postcode    ON planning_applications(postcode);
CREATE INDEX idx_apps_submitted   ON planning_applications(submitted_date DESC);
CREATE INDEX idx_apps_status      ON planning_applications(status);
CREATE INDEX idx_apps_type        ON planning_applications(application_type);
-- Spatial index on lat/lng for radius queries
CREATE INDEX idx_apps_location    ON planning_applications(lat, lng)
    WHERE lat IS NOT NULL AND lng IS NOT NULL;

-- ─────────────────────────────────────────────
-- Alert subscriptions
-- ─────────────────────────────────────────────
CREATE TABLE alert_subscriptions (
    id                  SERIAL PRIMARY KEY,
    email               TEXT NOT NULL,
    postcode            TEXT NOT NULL,
    lat                 DOUBLE PRECISION,
    lng                 DOUBLE PRECISION,
    radius_miles        SMALLINT DEFAULT 1,
    -- Filtering
    application_types   TEXT[] DEFAULT NULL,       -- NULL = all types
    -- Delivery
    frequency           TEXT DEFAULT 'weekly'
                            CHECK (frequency IN ('instant','daily','weekly')),
    -- Tier
    tier                TEXT DEFAULT 'free'
                            CHECK (tier IN ('free','homeowner','landlord','pro')),
    -- Stripe
    stripe_customer_id  TEXT,
    stripe_sub_id       TEXT,
    -- Email confirmation
    confirmed           BOOLEAN DEFAULT FALSE,
    confirm_token       TEXT UNIQUE DEFAULT gen_random_uuid()::text,
    unsubscribe_token   TEXT UNIQUE DEFAULT gen_random_uuid()::text,
    -- Metadata
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(email, postcode)
);

CREATE INDEX idx_subs_postcode  ON alert_subscriptions(postcode);
CREATE INDEX idx_subs_location  ON alert_subscriptions(lat, lng)
    WHERE lat IS NOT NULL AND lng IS NOT NULL;
CREATE INDEX idx_subs_confirmed ON alert_subscriptions(confirmed);
CREATE INDEX idx_subs_frequency ON alert_subscriptions(frequency);

-- ─────────────────────────────────────────────
-- Alert log — prevents duplicate sends
-- ─────────────────────────────────────────────
CREATE TABLE alert_log (
    id                  SERIAL PRIMARY KEY,
    subscription_id     INTEGER REFERENCES alert_subscriptions(id) ON DELETE CASCADE,
    application_id      INTEGER REFERENCES planning_applications(id) ON DELETE CASCADE,
    sent_at             TIMESTAMPTZ DEFAULT NOW(),
    sent_date           DATE NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE(subscription_id, application_id)
);

CREATE INDEX idx_alert_log_sub ON alert_log(subscription_id);
CREATE INDEX idx_alert_log_app ON alert_log(application_id);

-- ─────────────────────────────────────────────
-- Scrape log — track each run
-- ─────────────────────────────────────────────
CREATE TABLE scrape_log (
    id                  SERIAL PRIMARY KEY,
    council_id          INTEGER REFERENCES councils(id) ON DELETE SET NULL,
    source              TEXT,                      -- idox_scraper | northgate_scraper | gov_api
    started_at          TIMESTAMPTZ DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    applications_found  INTEGER DEFAULT 0,
    applications_new    INTEGER DEFAULT 0,
    status              TEXT DEFAULT 'running',    -- running | success | failed
    error_message       TEXT
);

-- ─────────────────────────────────────────────
-- Waitlist — for councils we don't yet cover
-- ─────────────────────────────────────────────
CREATE TABLE coverage_waitlist (
    id          SERIAL PRIMARY KEY,
    email       TEXT NOT NULL,
    postcode    TEXT NOT NULL,
    council_id  INTEGER REFERENCES councils(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(email, council_id)
);

-- ─────────────────────────────────────────────
-- Helper: find applications within X miles
-- Uses plain lat/lng arithmetic (no gist needed)
-- ─────────────────────────────────────────────
CREATE OR REPLACE FUNCTION applications_near(
    p_lat       DOUBLE PRECISION,
    p_lng       DOUBLE PRECISION,
    p_miles     DOUBLE PRECISION DEFAULT 1.0,
    p_days_back INTEGER DEFAULT 30
)
RETURNS TABLE (
    application_id  INTEGER,
    distance_miles  DOUBLE PRECISION
) AS $$
    SELECT
        a.id,
        (
            3959 * acos(
                cos(radians(p_lat)) * cos(radians(a.lat)) *
                cos(radians(a.lng) - radians(p_lng)) +
                sin(radians(p_lat)) * sin(radians(a.lat))
            )
        ) AS distance_miles
    FROM planning_applications a
    WHERE
        a.lat IS NOT NULL
        AND a.lng IS NOT NULL
        AND a.submitted_date >= CURRENT_DATE - p_days_back
        AND (
            3959 * acos(
                LEAST(1.0,
                    cos(radians(p_lat)) * cos(radians(a.lat)) *
                    cos(radians(a.lng) - radians(p_lng)) +
                    sin(radians(p_lat)) * sin(radians(a.lat))
                )
            )
        ) <= p_miles
    ORDER BY distance_miles;
$$ LANGUAGE SQL STABLE;

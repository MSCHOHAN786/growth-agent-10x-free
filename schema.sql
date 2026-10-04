-- =============================================================================
-- schema.sql — Growth Agent 10x Free (PostgreSQL / Supabase)
-- Idempotent waar mogelijk: tabellen met IF NOT EXISTS, seed met ON CONFLICT.
-- Draai dit in de Supabase SQL-editor of via psql.
-- =============================================================================

-- Nodig voor gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- channels: de 10 Nederlandse psychologie-kanalen
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS channels (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    sub_niche           TEXT NOT NULL,
    language            TEXT NOT NULL DEFAULT 'nl',
    status              TEXT NOT NULL DEFAULT 'active',
    youtube_channel_id  TEXT,
    youtube_oauth_encrypted TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- videos: gegenereerde video's per kanaal
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS videos (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id      TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    description     TEXT,
    tags            TEXT[],
    script          TEXT,
    status          TEXT NOT NULL DEFAULT 'draft',
    scheduled_at    TIMESTAMPTZ,
    published_at    TIMESTAMPTZ,
    youtube_video_id TEXT,
    file_path     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT videos_status_check CHECK (status IN ('draft', 'pending_approval', 'scheduled', 'published'))
);

-- ---------------------------------------------------------------------------
-- analytics: dagelijkse statistieken per video
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analytics (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id            UUID NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    views               INTEGER NOT NULL DEFAULT 0,
    likes               INTEGER NOT NULL DEFAULT 0,
    comments            INTEGER NOT NULL DEFAULT 0,
    watch_time_minutes  INTEGER NOT NULL DEFAULT 0,
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- approval_queue: menselijke goedkeuring vóór publicatie
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approval_queue (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id      UUID NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at    TIMESTAMPTZ,
    decision      TEXT,
    reviewer_note TEXT,
    payload       JSONB,
    CONSTRAINT approval_decision_check CHECK (decision IS NULL OR decision IN ('approved', 'rejected', 'needs_edit'))
);

-- ---------------------------------------------------------------------------
-- engagement_queue: reacties op video's + concept-antwoorden ter goedkeuring
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS engagement_queue (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id      UUID NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    comment_id    TEXT,
    comment_text  TEXT,
    comment_author TEXT,
    draft_reply   TEXT,
    status        TEXT NOT NULL DEFAULT 'pending_approval',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at    TIMESTAMPTZ,
    CONSTRAINT engagement_status_check CHECK (status IN ('pending_approval', 'approved', 'rejected', 'posted', 'needs_edit'))
);

-- ---------------------------------------------------------------------------
-- job_queue: asynchrone taken (research, render, upload, comments, ...)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_queue (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type    TEXT NOT NULL,
    channel_id  TEXT REFERENCES channels(id) ON DELETE SET NULL,
    payload     JSONB,
    status      TEXT NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT job_status_check CHECK (status IN ('pending', 'running', 'done', 'failed'))
);

-- ---------------------------------------------------------------------------
-- llm_usage: dagelijkse LLM-verbruik per provider (free-tier bewaking)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS llm_usage (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider    TEXT NOT NULL,
    day         DATE NOT NULL,
    requests    INTEGER NOT NULL DEFAULT 0,
    tokens      INTEGER NOT NULL DEFAULT 0,
    quota_hit   BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (provider, day)
);

-- ---------------------------------------------------------------------------
-- niche_patterns: geleerde patronen per kanaal (wat werkt / wat niet)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS niche_patterns (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id    TEXT REFERENCES channels(id) ON DELETE CASCADE,
    pattern_key   TEXT NOT NULL,
    pattern_value JSONB,
    hits          INTEGER NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- api_usage: dagelijkse API-verbruik per dienst (YouTube units, Supabase, ...)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_usage (
    id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service  TEXT NOT NULL,
    day      DATE NOT NULL,
    units    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (service, day)
);

-- ---------------------------------------------------------------------------
-- Indexen
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_videos_channel_status ON videos (channel_id, status);
CREATE INDEX IF NOT EXISTS idx_approval_queue_decision ON approval_queue (decision);
CREATE INDEX IF NOT EXISTS idx_engagement_queue_status ON engagement_queue (status);
CREATE INDEX IF NOT EXISTS idx_job_queue_status_type ON job_queue (status, job_type);
CREATE INDEX IF NOT EXISTS idx_llm_usage_day ON llm_usage (day);
CREATE INDEX IF NOT EXISTS idx_analytics_video_id ON analytics (video_id);
CREATE INDEX IF NOT EXISTS idx_videos_scheduled_at ON videos (scheduled_at);

-- ---------------------------------------------------------------------------
-- updated_at trigger: houdt updated_at automatisch bij
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_channels_updated_at ON channels;
CREATE TRIGGER trg_channels_updated_at
    BEFORE UPDATE ON channels
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_videos_updated_at ON videos;
CREATE TRIGGER trg_videos_updated_at
    BEFORE UPDATE ON videos
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_job_queue_updated_at ON job_queue;
CREATE TRIGGER trg_job_queue_updated_at
    BEFORE UPDATE ON job_queue
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_niche_patterns_updated_at ON niche_patterns;
CREATE TRIGGER trg_niche_patterns_updated_at
    BEFORE UPDATE ON niche_patterns
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- Seed: de 10 kanalen (namen komen overeen met config/channels.yaml)
-- Idempotent via ON CONFLICT (id) DO NOTHING.
-- ---------------------------------------------------------------------------
INSERT INTO channels (id, name, sub_niche, language, status) VALUES
    ('psy_nl_01', 'Donkere Geest',     'dark_psychology',        'nl', 'active'),
    ('psy_nl_02', 'Zelfverzekerd NL',  'zelfvertrouwen',         'nl', 'active'),
    ('psy_nl_03', 'Hart & Hechting',   'relaties',               'nl', 'active'),
    ('psy_nl_04', 'De Stoïcijn',       'stoicisme',              'nl', 'active'),
    ('psy_nl_05', 'Gewoonte Lab',      'gewoontes',              'nl', 'active'),
    ('psy_nl_06', 'Rust in je Hoofd',  'angst_stress',           'nl', 'active'),
    ('psy_nl_07', 'De Overtuiger',     'overtuigingskracht',     'nl', 'active'),
    ('psy_nl_08', 'Mindset Motor',     'mindset_motivatie',      'nl', 'active'),
    ('psy_nl_09', 'EQ Academie',       'emotionele_intelligentie','nl','active'),
    ('psy_nl_10', 'Slaap & Stilte',    'slaap_meditatie',        'nl', 'active')
ON CONFLICT (id) DO NOTHING;

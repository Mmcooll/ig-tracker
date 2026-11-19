-- Idempotent: safe to run on every deploy (python -m igtracker init-db).

CREATE TABLE IF NOT EXISTS posts (
  id            text PRIMARY KEY,
  format        text NOT NULL CHECK (format IN ('REEL', 'CAROUSEL', 'IMAGE')),
  posted_at     timestamptz NOT NULL,
  caption       text,
  permalink     text,
  first_seen_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
  post_id      text NOT NULL REFERENCES posts (id),
  checkpoint_h int  NOT NULL,
  taken_at     timestamptz NOT NULL,
  age_h        double precision NOT NULL,
  late         boolean NOT NULL,
  views int, reach int, likes int, comments int, shares int, saves int, followers int,
  PRIMARY KEY (post_id, checkpoint_h)
);

-- Baseline frozen at snapshot time, so later posts never rewrite history.
CREATE TABLE IF NOT EXISTS comparisons (
  post_id      text NOT NULL,
  checkpoint_h int  NOT NULL,
  metric       text NOT NULL CHECK (metric IN ('views', 'reach', 'er_reach')),
  value        double precision,
  scope        text NOT NULL CHECK (scope IN ('format', 'account')),
  n            int  NOT NULL,
  median       double precision,
  mad          double precision,
  z            double precision,
  pct_rank     double precision,
  PRIMARY KEY (post_id, checkpoint_h, metric),
  FOREIGN KEY (post_id, checkpoint_h) REFERENCES snapshots (post_id, checkpoint_h)
);

CREATE TABLE IF NOT EXISTS alerts (
  post_id      text NOT NULL,
  checkpoint_h int  NOT NULL,
  direction    text NOT NULL,
  sent_at      timestamptz NOT NULL,
  PRIMARY KEY (post_id, checkpoint_h)
);

CREATE TABLE IF NOT EXISTS kv (key text PRIMARY KEY, value text);

CREATE OR REPLACE VIEW v_post_report AS
SELECT p.id AS post_id, p.format, p.posted_at, p.permalink, left(p.caption, 80) AS caption,
       s.checkpoint_h, round(s.age_h::numeric, 1) AS age_h, s.late,
       s.views, s.reach, s.likes, s.comments, s.shares, s.saves, s.followers,
       s.likes + s.comments + s.shares + s.saves AS interactions,
       round(100.0 * (s.likes + s.comments + s.shares + s.saves) / nullif(s.reach, 0), 2) AS er_reach_pct,
       round(100.0 * (s.likes + s.comments + s.shares + s.saves) / nullif(s.followers, 0), 2) AS er_followers_pct,
       round((ce.median * 100)::numeric, 2) AS er_median_pct,
       round((100 * (ce.value / nullif(ce.median, 0) - 1))::numeric) AS er_vs_median_pct,
       round(ce.pct_rank::numeric) AS er_pct_rank,
       round(ce.z::numeric, 2) AS er_z,
       round(cv.median::numeric) AS views_median,
       round((100 * (cv.value / nullif(cv.median, 0) - 1))::numeric) AS views_vs_median_pct,
       round(cv.pct_rank::numeric) AS views_pct_rank,
       round(cv.z::numeric, 2) AS views_z,
       ce.scope AS baseline_scope, ce.n AS baseline_n,
       a.direction AS alert
FROM posts p
JOIN snapshots s ON s.post_id = p.id
LEFT JOIN comparisons cv ON cv.post_id = s.post_id AND cv.checkpoint_h = s.checkpoint_h AND cv.metric = 'views'
LEFT JOIN comparisons ce ON ce.post_id = s.post_id AND ce.checkpoint_h = s.checkpoint_h AND ce.metric = 'er_reach'
LEFT JOIN alerts a ON a.post_id = s.post_id AND a.checkpoint_h = s.checkpoint_h;

CREATE OR REPLACE VIEW v_format_summary AS
SELECT p.format, s.checkpoint_h, count(*) AS posts,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY s.views)::numeric) AS median_views,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY s.reach)::numeric) AS median_reach,
       round((100 * percentile_cont(0.5) WITHIN GROUP (
         ORDER BY (s.likes + s.comments + s.shares + s.saves)::float8 / nullif(s.reach, 0)))::numeric, 2) AS median_er_reach_pct
FROM posts p
JOIN snapshots s ON s.post_id = p.id
WHERE NOT s.late AND p.posted_at > now() - interval '180 days'
GROUP BY p.format, s.checkpoint_h;

-- Read-only login for Looker Studio; set its password once by hand: ALTER ROLE dashboard_reader PASSWORD '...';
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dashboard_reader') THEN
    CREATE ROLE dashboard_reader LOGIN;
  END IF;
END $$;
GRANT SELECT ON v_post_report, v_format_summary TO dashboard_reader;

-- Supabase exposes the public schema over its web API to the anon/authenticated roles.
-- It is never use, so lock it out. RLS with no policies = deny; the owner (postgres) bypasses it.
ALTER TABLE posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE comparisons ENABLE ROW LEVEL SECURITY;
ALTER TABLE alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE kv ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
  END IF;
END $$;

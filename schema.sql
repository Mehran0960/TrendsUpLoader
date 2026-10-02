CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  external_id TEXT NOT NULL,
  trend_key TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT,
  published_at TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  signal_value REAL DEFAULT 0,
  previous_value REAL DEFAULT 0,
  velocity_pct REAL DEFAULT 0,
  category TEXT,
  risk_flags TEXT,
  score REAL DEFAULT 0,
  UNIQUE(source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_signals_source_seen ON signals(source,last_seen_at);
CREATE INDEX IF NOT EXISTS idx_signals_score ON signals(score DESC);
CREATE INDEX IF NOT EXISTS idx_signals_trend_key ON signals(trend_key);

CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  external_id TEXT NOT NULL,
  trend_key TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  signal_value REAL DEFAULT 0,
  score REAL DEFAULT 0,
  risk_flags TEXT
);
CREATE INDEX IF NOT EXISTS idx_observations_entity_time ON observations(source,external_id,observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_observations_key_time ON observations(trend_key,observed_at DESC);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  source_count INTEGER DEFAULT 0,
  signal_count INTEGER DEFAULT 0,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);

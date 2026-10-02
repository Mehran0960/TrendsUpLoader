CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'started',
  source_count INTEGER NOT NULL DEFAULT 0,
  candidate_count INTEGER NOT NULL DEFAULT 0,
  error_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  published_at TEXT,
  discovered_at TEXT NOT NULL,
  score REAL NOT NULL DEFAULT 0,
  novelty REAL NOT NULL DEFAULT 0,
  velocity REAL NOT NULL DEFAULT 0,
  cross_source REAL NOT NULL DEFAULT 0,
  monetization REAL NOT NULL DEFAULT 0,
  risk REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'new',
  UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_candidates_discovered ON candidates(discovered_at);
CREATE INDEX IF NOT EXISTS idx_candidates_score ON candidates(score DESC);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status);

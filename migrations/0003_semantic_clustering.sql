ALTER TABLE signals ADD COLUMN embedding_json TEXT;
ALTER TABLE signals ADD COLUMN semantic_cluster TEXT;
ALTER TABLE signals ADD COLUMN semantic_similarity REAL NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_signals_semantic_cluster ON signals(semantic_cluster);

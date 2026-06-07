CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  title TEXT,
  kind TEXT NOT NULL,
  hash TEXT NOT NULL,
  updated_at TEXT,
  indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL,
  path TEXT NOT NULL,
  title TEXT,
  heading TEXT,
  anchor TEXT,
  ordinal INTEGER NOT NULL,
  summary TEXT,
  content TEXT NOT NULL,
  tokens_estimate INTEGER,
  updated_at TEXT,
  indexed_at TEXT NOT NULL,
  FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_id UNINDEXED,
  title,
  path,
  heading,
  summary,
  content
);

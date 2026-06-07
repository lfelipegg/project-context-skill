#!/usr/bin/env python3
"""Project-local context CLI for Codex.

Standard-library only. Stores a SQLite FTS index of selected Markdown files so
agents can search first and read bounded chunks by ID.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Iterable

DEFAULT_INCLUDE = [
    "README.md",
    "AGENTS.md",
    "docs/**/*.md",
    "docs/**/*.mdx",
    "agents/**/*.md",
    ".codex-context/notes/**/*.md",
]
DEFAULT_EXCLUDE = [
    ".git/**",
    ".worktrees/**",
    "node_modules/**",
    "vendor/**",
    "dist/**",
    "build/**",
    ".next/**",
    "coverage/**",
    ".venv/**",
    "venv/**",
    "**/.env*",
    "**/*secret*",
    "**/*credential*",
    "**/*private-key*",
]
DEFAULT_CONFIG = """version = 1

db_path = ".codex-context/context.sqlite"

[sources]
include = [
  "README.md",
  "AGENTS.md",
  "docs/**/*.md",
  "docs/**/*.mdx",
  "agents/**/*.md",
  ".codex-context/notes/**/*.md"
]
exclude = [
  ".git/**",
  ".worktrees/**",
  "node_modules/**",
  "vendor/**",
  "dist/**",
  "build/**",
  ".next/**",
  "coverage/**",
  ".venv/**",
  "venv/**",
  "**/.env*",
  "**/*secret*",
  "**/*credential*",
  "**/*private-key*"
]

[chunking]
target_chars = 3500
max_chars = 5500

[output]
search_limit_default = 8
read_max_chars_default = 4000
read_max_chars_hard = 12000
"""

SCHEMA = """
PRAGMA foreign_keys = ON;
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
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_id UNINDEXED,
  title,
  path,
  heading,
  summary,
  content
);
"""


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".codex-context").exists() or (candidate / ".git").exists() or (candidate / "AGENTS.md").exists():
            return candidate
    return current


def load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import tomllib  # Python 3.11+

        with path.open("rb") as fh:
            return tomllib.load(fh)
    except Exception:
        return {}


def config(root: Path) -> dict[str, Any]:
    cfg = load_toml(root / ".codex-context" / "config.toml")
    sources = cfg.get("sources", {}) if isinstance(cfg.get("sources", {}), dict) else {}
    chunking = cfg.get("chunking", {}) if isinstance(cfg.get("chunking", {}), dict) else {}
    output = cfg.get("output", {}) if isinstance(cfg.get("output", {}), dict) else {}
    return {
        "db_path": cfg.get("db_path", ".codex-context/context.sqlite"),
        "include": sources.get("include", DEFAULT_INCLUDE),
        "exclude": sources.get("exclude", DEFAULT_EXCLUDE),
        "target_chars": int(chunking.get("target_chars", 3500)),
        "max_chars": int(chunking.get("max_chars", 5500)),
        "search_limit_default": int(output.get("search_limit_default", 8)),
        "read_max_chars_default": int(output.get("read_max_chars_default", 4000)),
        "read_max_chars_hard": int(output.get("read_max_chars_hard", 12000)),
    }


def db_path(root: Path, cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or config(root)
    p = Path(str(cfg["db_path"]))
    return p if p.is_absolute() else root / p


def connect(root: Path) -> sqlite3.Connection:
    cfg = config(root)
    path = db_path(root, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        con.executescript(FTS_SCHEMA)
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('fts5', '1')")
    except sqlite3.OperationalError:
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('fts5', '0')")
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', '1')")
    con.commit()
    return con


def ensure_project_files(root: Path) -> None:
    ctx_dir = root / ".codex-context"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    notes_dir = ctx_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    cfg = ctx_dir / "config.toml"
    if not cfg.exists():
        cfg.write_text(DEFAULT_CONFIG, encoding="utf-8")
    readme = ctx_dir / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Codex Context\n\n"
            "This directory stores the project-local context retrieval system.\n\n"
            "- `ctx.py` searches and reads a SQLite index of Markdown documentation.\n"
            "- `context.sqlite` is generated and should usually stay out of git.\n"
            "- `notes/` can hold extra Markdown notes that should be indexed.\n\n"
            "Common commands:\n\n"
            "```bash\n"
            "python .codex-context/ctx.py status\n"
            "python .codex-context/ctx.py ingest\n"
            "python .codex-context/ctx.py search \"query\" --limit 8\n"
            "python .codex-context/ctx.py read <id> --max-chars 4000\n"
            "```\n",
            encoding="utf-8",
        )


def rel_posix(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def is_excluded(rel: str, excludes: Iterable[str]) -> bool:
    parts = rel.split("/")
    for pattern in excludes:
        if fnmatch.fnmatch(rel, pattern):
            return True
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if rel == prefix or rel.startswith(prefix + "/"):
                return True
        if any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def discover_sources(root: Path, cfg: dict[str, Any]) -> list[Path]:
    found: dict[str, Path] = {}
    for pattern in cfg["include"]:
        pattern = str(pattern)
        candidates: Iterable[Path]
        if any(ch in pattern for ch in "*?["):
            candidates = root.glob(pattern)
        else:
            candidates = [root / pattern]
        for p in candidates:
            if not p.is_file():
                continue
            if p.suffix.lower() not in {".md", ".mdx"}:
                continue
            try:
                rel = rel_posix(root, p)
            except ValueError:
                continue
            if is_excluded(rel, cfg["exclude"]):
                continue
            found[rel] = p
    return [found[k] for k in sorted(found)]


def file_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[`*_~]+", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:80]


def extract_title(path: Path, text: str) -> str:
    m = re.search(r"^#\s+(.+?)\s*$", text, flags=re.MULTILINE)
    if m:
        return m.group(1).strip()
    return path.stem.replace("-", " ").replace("_", " ").strip().title()


def doc_kind(rel: str) -> str:
    if rel == "AGENTS.md":
        return "agents-root"
    if rel.startswith("docs/tasks/"):
        return "task-doc"
    if rel.startswith("docs/agents/") or rel.startswith("agents/"):
        return "agent-doc"
    if rel.startswith(".codex-context/notes/"):
        return "context-note"
    if rel.lower() == "readme.md":
        return "readme"
    return "doc"


def clean_for_summary(text: str, max_chars: int = 520) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"[#>*_\-[\]()]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def split_markdown(text: str, cfg: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return (heading, anchor, content) chunks."""
    matches = list(re.finditer(r"^(#{1,6})\s+(.+?)\s*$", text, flags=re.MULTILINE))
    sections: list[tuple[str, str]] = []
    if not matches:
        sections = [("Document", text)]
    else:
        if matches[0].start() > 0:
            preface = text[: matches[0].start()].strip()
            if preface:
                sections.append(("Preface", preface))
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            heading = m.group(2).strip()
            content = text[start:end].strip()
            sections.append((heading, content))

    target = int(cfg["target_chars"])
    max_chars = int(cfg["max_chars"])
    chunks: list[tuple[str, str, str]] = []
    for heading, content in sections:
        if len(content) <= max_chars:
            chunks.append((heading, slugify(heading), content))
            continue
        paras = re.split(r"\n\s*\n", content)
        buf: list[str] = []
        size = 0
        part = 1
        for para in paras:
            para = para.strip()
            if not para:
                continue
            if buf and size + len(para) + 2 > target:
                h = f"{heading} part {part}"
                chunks.append((h, slugify(h), "\n\n".join(buf)))
                part += 1
                buf = []
                size = 0
            if len(para) > max_chars:
                for i in range(0, len(para), target):
                    h = f"{heading} part {part}"
                    chunks.append((h, slugify(h), para[i : i + target]))
                    part += 1
                continue
            buf.append(para)
            size += len(para) + 2
        if buf:
            h = f"{heading} part {part}" if part > 1 else heading
            chunks.append((h, slugify(h), "\n\n".join(buf)))
    return chunks


def delete_document(con: sqlite3.Connection, document_id: int) -> None:
    ids = [row[0] for row in con.execute("SELECT id FROM chunks WHERE document_id = ?", (document_id,))]
    for chunk_id in ids:
        try:
            con.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
        except sqlite3.OperationalError:
            pass
    con.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
    con.execute("DELETE FROM documents WHERE id = ?", (document_id,))


def ingest_file(con: sqlite3.Connection, root: Path, path: Path, cfg: dict[str, Any], force: bool = False) -> tuple[str, int]:
    rel = rel_posix(root, path)
    text = path.read_text(encoding="utf-8", errors="ignore")
    digest = file_hash(text)
    old = con.execute("SELECT id, hash FROM documents WHERE path = ?", (rel,)).fetchone()
    if old and old["hash"] == digest and not force:
        count = con.execute("SELECT COUNT(*) FROM chunks WHERE document_id = ?", (old["id"],)).fetchone()[0]
        return "unchanged", int(count)
    if old:
        delete_document(con, int(old["id"]))
    title = extract_title(path, text)
    ts = now_iso()
    updated_at = _dt.datetime.fromtimestamp(path.stat().st_mtime, _dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    cur = con.execute(
        "INSERT INTO documents(path, title, kind, hash, updated_at, indexed_at) VALUES (?, ?, ?, ?, ?, ?)",
        (rel, title, doc_kind(rel), digest, updated_at, ts),
    )
    document_id = int(cur.lastrowid)
    chunks = split_markdown(text, cfg)
    for ordinal, (heading, anchor, content) in enumerate(chunks, start=1):
        summary = clean_for_summary(content)
        cur = con.execute(
            """
            INSERT INTO chunks(document_id, path, title, heading, anchor, ordinal, summary, content, tokens_estimate, updated_at, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (document_id, rel, title, heading, anchor, ordinal, summary, content, estimate_tokens(content), updated_at, ts),
        )
        chunk_id = int(cur.lastrowid)
        try:
            con.execute(
                "INSERT INTO chunks_fts(chunk_id, title, path, heading, summary, content) VALUES (?, ?, ?, ?, ?, ?)",
                (chunk_id, title, rel, heading, summary, content),
            )
        except sqlite3.OperationalError:
            pass
    return "indexed", len(chunks)


def cmd_init(args: argparse.Namespace) -> int:
    root = find_repo_root(Path(args.repo) if args.repo else None)
    ensure_project_files(root)
    with connect(root) as con:
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('last_init', ?)", (now_iso(),))
        con.commit()
    print(f"Initialized context system at {root / '.codex-context'}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    root = find_repo_root(Path(args.repo) if args.repo else None)
    ensure_project_files(root)
    cfg = config(root)
    sources = discover_sources(root, cfg)
    with connect(root) as con:
        existing = {row["path"]: row["id"] for row in con.execute("SELECT id, path FROM documents")}
        seen: set[str] = set()
        indexed = unchanged = chunks = 0
        for p in sources:
            rel = rel_posix(root, p)
            seen.add(rel)
            status, count = ingest_file(con, root, p, cfg, force=bool(args.force))
            chunks += count
            if status == "indexed":
                indexed += 1
            else:
                unchanged += 1
        removed = 0
        for rel, document_id in existing.items():
            if rel not in seen:
                delete_document(con, int(document_id))
                removed += 1
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('last_ingest', ?)", (now_iso(),))
        con.commit()
    print(f"Indexed {indexed} changed docs, {unchanged} unchanged docs, {removed} removed docs, {chunks} chunks available.")
    return 0


def sanitize_fts(query: str) -> str:
    terms = re.findall(r"[A-Za-z0-9_]{2,}", query.lower())
    seen: list[str] = []
    for term in terms:
        if term not in seen:
            seen.append(term)
    return " OR ".join(f"{term}*" for term in seen[:10])


def search_rows(con: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
    fts_query = sanitize_fts(query)
    if fts_query:
        try:
            rows = list(
                con.execute(
                    """
                    SELECT c.id, c.title, c.path, c.heading, c.summary, c.updated_at, c.tokens_estimate,
                           bm25(chunks_fts) AS score
                    FROM chunks_fts
                    JOIN chunks c ON c.id = chunks_fts.chunk_id
                    WHERE chunks_fts MATCH ?
                    ORDER BY score
                    LIMIT ?
                    """,
                    (fts_query, limit),
                )
            )
            if rows:
                return rows
        except sqlite3.OperationalError:
            pass
    words = re.findall(r"[A-Za-z0-9_]{2,}", query.lower())[:6]
    if not words:
        return []
    clauses = []
    params: list[Any] = []
    for word in words:
        clauses.append("lower(c.title || ' ' || c.path || ' ' || ifnull(c.heading,'') || ' ' || ifnull(c.summary,'') || ' ' || c.content) LIKE ?")
        params.append(f"%{word}%")
    sql = f"""
        SELECT c.id, c.title, c.path, c.heading, c.summary, c.updated_at, c.tokens_estimate, 0 AS score
        FROM chunks c
        WHERE {' OR '.join(clauses)}
        ORDER BY c.updated_at DESC, c.id ASC
        LIMIT ?
    """
    params.append(limit)
    return list(con.execute(sql, params))


def row_to_search_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "path": row["path"],
        "heading": row["heading"],
        "summary": row["summary"],
        "updated_at": row["updated_at"],
        "tokens_estimate": row["tokens_estimate"],
    }


def cmd_search(args: argparse.Namespace) -> int:
    root = find_repo_root(Path(args.repo) if args.repo else None)
    cfg = config(root)
    limit = int(args.limit or cfg["search_limit_default"])
    with connect(root) as con:
        rows = search_rows(con, args.query, limit)
    if args.json:
        print(json.dumps([row_to_search_dict(r) for r in rows], indent=2))
        return 0
    if not rows:
        print("No context matches found.")
        return 1
    for row in rows:
        heading = f" — {row['heading']}" if row["heading"] and row["heading"] != row["title"] else ""
        print(f"[{row['id']}] {row['title']}{heading}")
        print(f"path: {row['path']}")
        print(f"updated: {row['updated_at']} | approx_tokens: {row['tokens_estimate']}")
        print(f"summary: {row['summary']}")
        print()
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    root = find_repo_root(Path(args.repo) if args.repo else None)
    cfg = config(root)
    max_chars = int(args.max_chars or cfg["read_max_chars_default"])
    max_chars = min(max_chars, int(cfg["read_max_chars_hard"]))
    with connect(root) as con:
        row = con.execute(
            "SELECT id, title, path, heading, anchor, summary, content, updated_at, tokens_estimate FROM chunks WHERE id = ?",
            (int(args.id),),
        ).fetchone()
    if not row:
        print(f"No chunk found for id {args.id}.", file=sys.stderr)
        return 1
    content = row["content"]
    truncated = len(content) > max_chars
    if truncated:
        content = content[: max_chars - 1].rstrip() + "…"
    data = {
        "id": row["id"],
        "title": row["title"],
        "path": row["path"],
        "heading": row["heading"],
        "anchor": row["anchor"],
        "summary": row["summary"],
        "updated_at": row["updated_at"],
        "tokens_estimate": row["tokens_estimate"],
        "truncated": truncated,
        "content": content,
    }
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"[{row['id']}] {row['title']} — {row['heading']}")
        print(f"path: {row['path']}#{row['anchor']}")
        print(f"updated: {row['updated_at']} | approx_tokens: {row['tokens_estimate']} | truncated: {str(truncated).lower()}")
        print(f"summary: {row['summary']}")
        print("\n---\n")
        print(content)
    return 0


def cmd_related(args: argparse.Namespace) -> int:
    root = find_repo_root(Path(args.repo) if args.repo else None)
    limit = int(args.limit or 5)
    with connect(root) as con:
        src = con.execute("SELECT document_id, ordinal FROM chunks WHERE id = ?", (int(args.id),)).fetchone()
        if not src:
            print(f"No chunk found for id {args.id}.", file=sys.stderr)
            return 1
        rows = list(
            con.execute(
                """
                SELECT id, title, path, heading, summary, updated_at, tokens_estimate,
                       abs(ordinal - ?) AS distance
                FROM chunks
                WHERE document_id = ? AND id != ?
                ORDER BY distance ASC, ordinal ASC
                LIMIT ?
                """,
                (int(src["ordinal"]), int(src["document_id"]), int(args.id), limit),
            )
        )
    if args.json:
        print(json.dumps([row_to_search_dict(r) for r in rows], indent=2))
        return 0
    if not rows:
        print("No related chunks found.")
        return 1
    for row in rows:
        print(f"[{row['id']}] {row['title']} — {row['heading']}")
        print(f"path: {row['path']}")
        print(f"summary: {row['summary']}")
        print()
    return 0


def db_counts(con: sqlite3.Connection) -> dict[str, Any]:
    counts = {
        "documents": con.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "chunks": con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "fts5": con.execute("SELECT value FROM meta WHERE key = 'fts5'").fetchone(),
        "last_ingest": con.execute("SELECT value FROM meta WHERE key = 'last_ingest'").fetchone(),
    }
    counts["fts5"] = counts["fts5"][0] if counts["fts5"] else "unknown"
    counts["last_ingest"] = counts["last_ingest"][0] if counts["last_ingest"] else None
    return counts


def stale_count(root: Path, con: sqlite3.Connection, cfg: dict[str, Any]) -> tuple[int, int]:
    sources = discover_sources(root, cfg)
    stale = 0
    for p in sources:
        rel = rel_posix(root, p)
        row = con.execute("SELECT hash FROM documents WHERE path = ?", (rel,)).fetchone()
        text = p.read_text(encoding="utf-8", errors="ignore")
        if not row or row["hash"] != file_hash(text):
            stale += 1
    return stale, len(sources)


def cmd_status(args: argparse.Namespace) -> int:
    root = find_repo_root(Path(args.repo) if args.repo else None)
    cfg = config(root)
    with connect(root) as con:
        counts = db_counts(con)
        stale, source_total = stale_count(root, con, cfg)
    data = {
        "repo": str(root),
        "db_path": str(db_path(root, cfg)),
        "documents": counts["documents"],
        "chunks": counts["chunks"],
        "source_files": source_total,
        "stale_or_missing_sources": stale,
        "fts5": counts["fts5"],
        "last_ingest": counts["last_ingest"],
    }
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        for key, value in data.items():
            print(f"{key}: {value}")
        if stale:
            print("status: stale; run `python .codex-context/ctx.py ingest`")
        else:
            print("status: fresh")
    return 0 if not stale else 2


def cmd_recent(args: argparse.Namespace) -> int:
    root = find_repo_root(Path(args.repo) if args.repo else None)
    limit = int(args.limit or 10)
    with connect(root) as con:
        rows = list(
            con.execute(
                """
                SELECT id, title, path, heading, summary, updated_at, tokens_estimate
                FROM chunks
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            )
        )
    if args.json:
        print(json.dumps([row_to_search_dict(r) for r in rows], indent=2))
        return 0
    for row in rows:
        print(f"[{row['id']}] {row['title']} — {row['heading']}")
        print(f"path: {row['path']} | updated: {row['updated_at']}")
        print(f"summary: {row['summary']}")
        print()
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    root = find_repo_root(Path(args.repo) if args.repo else None)
    cfg = config(root)
    path = db_path(root, cfg)
    if path.exists():
        path.unlink()
    ensure_project_files(root)
    with connect(root):
        pass
    args.force = True
    return cmd_ingest(args)


def cmd_doctor(args: argparse.Namespace) -> int:
    root = find_repo_root(Path(args.repo) if args.repo else None)
    ensure_project_files(root)
    cfg = config(root)
    problems: list[str] = []
    agents = root / "AGENTS.md"
    if not agents.exists():
        problems.append("AGENTS.md missing")
    else:
        text = agents.read_text(encoding="utf-8", errors="ignore")
        if text.count("project-context:start") != 1 or text.count("project-context:end") != 1:
            problems.append("AGENTS.md should contain exactly one marked project-context section")
    if not (root / ".codex-context" / "config.toml").exists():
        problems.append("missing .codex-context/config.toml")
    if not (root / ".codex-context" / "ctx.py").exists():
        problems.append("missing .codex-context/ctx.py")
    with connect(root) as con:
        counts = db_counts(con)
        stale, source_total = stale_count(root, con, cfg)
    print(f"repo: {root}")
    print(f"db: {db_path(root, cfg)}")
    print(f"documents: {counts['documents']} | chunks: {counts['chunks']} | source_files: {source_total}")
    print(f"fts5: {counts['fts5']} | last_ingest: {counts['last_ingest']}")
    if stale:
        problems.append(f"{stale} source files are stale or missing from index")
    if problems:
        print("problems:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("doctor: ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SQLite-backed project context search for Codex")
    parser.add_argument("--repo", help="Repository root. Defaults to auto-detected root.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Create context directories, config, and database schema")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("ingest", help="Index configured Markdown files into SQLite")
    p.add_argument("--force", action="store_true", help="Re-index unchanged files")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("rebuild", help="Delete and rebuild the SQLite index")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("search", help="Search indexed context")
    p.add_argument("query")
    p.add_argument("--limit", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("read", help="Read one chunk by ID")
    p.add_argument("id", type=int)
    p.add_argument("--max-chars", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("related", help="Show nearby chunks from the same document")
    p.add_argument("id", type=int)
    p.add_argument("--limit", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_related)

    p = sub.add_parser("recent", help="Show recently updated chunks")
    p.add_argument("--limit", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_recent)

    p = sub.add_parser("status", help="Show index status and freshness")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("doctor", help="Validate context setup")
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

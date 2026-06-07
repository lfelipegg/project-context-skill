#!/usr/bin/env python3
"""Bootstrap token-efficient project context retrieval in a repository.

Creates/updates:
- .codex-context/ctx.py
- .codex-context/config.toml
- .codex-context/README.md
- docs/agents/context.md
- AGENTS.md project-context section
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

START = "<!-- project-context:start -->"
END = "<!-- project-context:end -->"

AGENTS_SECTION = """## Project Context Retrieval

<!-- project-context:start -->
This repo uses token-efficient project context retrieval.

Use the user-level `$project-context` Skill before broad documentation scans or when a task depends on architecture, prior decisions, task history, database conventions, deployment behavior, auth/security/billing behavior, or non-obvious project behavior.

Do not use it for trivial single-file edits.

Preferred commands:

- `python .codex-context/ctx.py status`
- `python .codex-context/ctx.py search "<query>" --limit 8`
- `python .codex-context/ctx.py read <id> --max-chars 4000`
- `python .codex-context/ctx.py related <id> --limit 5`
- `python .codex-context/ctx.py ingest` after meaningful Markdown doc changes

Rules:

- Search first; read only directly relevant IDs.
- Never dump whole SQLite tables, full indexes, or every Markdown file.
- Treat Markdown files as the source of truth and SQLite as the retrieval index.
- If the index is missing or stale, rebuild it or fall back to targeted repo inspection.
<!-- project-context:end -->
"""

CONFIG = """version = 1

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

README = """# Codex Context

Purpose: project-local SQLite-backed context retrieval for Codex.

The Markdown files remain the source of truth. The SQLite database is a generated index used to search first and read only bounded chunks by ID.

## Commands

```bash
python .codex-context/ctx.py status
python .codex-context/ctx.py ingest
python .codex-context/ctx.py search "query" --limit 8
python .codex-context/ctx.py read <id> --max-chars 4000
python .codex-context/ctx.py related <id> --limit 5
python .codex-context/ctx.py doctor
```

## Files

- `ctx.py`: project-local context CLI.
- `config.toml`: include/exclude patterns and limits.
- `context.sqlite`: generated index; usually keep out of git.
- `notes/`: optional Markdown notes to index.

## Token Discipline

Search first. Read only directly relevant IDs. Do not dump whole tables or large docs.
"""

DOCS_CONTEXT = """# Project Context Documentation

Purpose: explain how this repo keeps Codex context token-efficient.
Read when: setting up context retrieval, reorganizing docs, or deciding where durable project knowledge belongs.
Do not read for: trivial source edits that do not need repository history or architecture context.
Source of truth: `AGENTS.md`, `.codex-context/config.toml`, and current repository files.
Last reviewed: update when the context system changes.

## Summary

- `AGENTS.md` should stay short and route Codex to focused docs or `$project-context`.
- Markdown files are the human-readable source of truth.
- `.codex-context/context.sqlite` is a generated retrieval index.
- Codex should search the index first and read only relevant chunks by ID.
- Re-run `python .codex-context/ctx.py ingest` after meaningful Markdown changes.

## Documentation Shape

Substantial docs should start with purpose, read-when guidance, source-of-truth notes, and a short summary. Use stable headings and one topic per section so the index returns useful chunks.

## What To Keep Out Of AGENTS.md

Keep long architecture history, migration detail, task logs, troubleshooting catalogs, and large examples in focused docs instead of the root agent guide.

## What To Keep Out Of The Index

Do not index secrets, `.env` files, credentials, dependency folders, build artifacts, coverage output, or generated logs.
"""


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def write_file(path: Path, content: str, overwrite: bool, dry_run: bool, changed: list[str]) -> None:
    if path.exists() and not overwrite:
        return
    if dry_run:
        changed.append(f"would write {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else None
    if old == content:
        return
    path.write_text(content, encoding="utf-8")
    changed.append(str(path))


def patch_agents(path: Path, dry_run: bool, changed: list[str]) -> None:
    if not path.exists():
        new_text = "# Repository Instructions\n\n" + AGENTS_SECTION.strip() + "\n"
        if dry_run:
            changed.append(f"would create {path}")
            return
        path.write_text(new_text, encoding="utf-8")
        changed.append(str(path))
        return

    text = path.read_text(encoding="utf-8", errors="ignore")
    if START in text and END in text:
        before = text.split(START, 1)[0]
        after = text.split(END, 1)[1]
        marked_body = AGENTS_SECTION.split(START, 1)[1].split(END, 1)[0]
        new_text = before.rstrip() + "\n\n" + START + marked_body + END + after
    else:
        insert_at = len(text)
        for marker in ["## Final Response", "## Response", "## Output", "## Quality Gates"]:
            idx = text.find(marker)
            if idx != -1:
                insert_at = idx
                break
        prefix = text[:insert_at].rstrip()
        suffix = text[insert_at:].lstrip()
        new_text = prefix + "\n\n" + AGENTS_SECTION.strip() + "\n\n" + suffix
        if not suffix:
            new_text = new_text.rstrip() + "\n"
    if new_text == text:
        return
    if dry_run:
        changed.append(f"would patch {path}")
        return
    path.write_text(new_text, encoding="utf-8")
    changed.append(str(path))


def update_gitignore(repo: Path, dry_run: bool, changed: list[str]) -> None:
    path = repo / ".gitignore"
    block = [".codex-context/context.sqlite", ".codex-context/*.sqlite", ".codex-context/*.db"]
    text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    lines = set(line.strip() for line in text.splitlines())
    missing = [line for line in block if line not in lines]
    if not missing:
        return
    addition = ("\n" if text and not text.endswith("\n") else "") + "\n# Generated Codex context index\n" + "\n".join(missing) + "\n"
    if dry_run:
        changed.append(f"would update {path}")
        return
    path.write_text(text + addition, encoding="utf-8")
    changed.append(str(path))


def copy_ctx(repo: Path, dry_run: bool, changed: list[str]) -> None:
    src = skill_root() / "scripts" / "ctx_runtime.py"
    dest = repo / ".codex-context" / "ctx.py"
    if dry_run:
        changed.append(f"would copy {src} -> {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    old = dest.read_text(encoding="utf-8", errors="ignore") if dest.exists() else None
    new = src.read_text(encoding="utf-8")
    if old == new:
        return
    shutil.copyfile(src, dest)
    dest.chmod(0o755)
    changed.append(str(dest))


def run_ctx(repo: Path, args: list[str]) -> int:
    cmd = [sys.executable, str(repo / ".codex-context" / "ctx.py"), "--repo", str(repo), *args]
    print("$ " + " ".join(cmd))
    return subprocess.call(cmd, cwd=repo)


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up SQLite-backed Codex context retrieval in a repo")
    parser.add_argument("--repo", default=".", help="Repository root")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    parser.add_argument("--overwrite-docs", action="store_true", help="Overwrite generated README/config/docs files")
    parser.add_argument("--no-ingest", action="store_true", help="Do not ingest Markdown after setup")
    parser.add_argument("--no-gitignore", action="store_true", help="Do not update .gitignore")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    changed: list[str] = []

    if not repo.exists():
        print(f"Repo path does not exist: {repo}", file=sys.stderr)
        return 1

    write_file(repo / ".codex-context" / "config.toml", CONFIG, args.overwrite_docs, args.dry_run, changed)
    write_file(repo / ".codex-context" / "README.md", README, args.overwrite_docs, args.dry_run, changed)
    write_file(repo / "docs" / "agents" / "context.md", DOCS_CONTEXT, args.overwrite_docs, args.dry_run, changed)
    if not args.dry_run:
        (repo / ".codex-context" / "notes").mkdir(parents=True, exist_ok=True)
    copy_ctx(repo, args.dry_run, changed)
    patch_agents(repo / "AGENTS.md", args.dry_run, changed)
    if not args.no_gitignore:
        update_gitignore(repo, args.dry_run, changed)

    if args.dry_run:
        print("Dry-run changes:")
        for item in changed:
            print(f"- {item}")
        return 0

    init_code = run_ctx(repo, ["init"])
    ingest_code = 0
    if not args.no_ingest:
        ingest_code = run_ctx(repo, ["ingest"])
    doctor_code = run_ctx(repo, ["doctor"])

    print("\nChanged files:")
    if changed:
        for item in changed:
            print(f"- {item}")
    else:
        print("- none")

    if init_code or ingest_code or doctor_code:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Project Context Skill

## Purpose

`project-context` helps Codex and Claude keep repository context small without losing access to durable project knowledge. It installs a project-local SQLite index over selected Markdown files, then teaches agents to search first and read only the small chunks they need.

Use this skill when a repository has useful context spread across `AGENTS.md`, `README.md`, architecture notes, task docs, or other Markdown files, and broad documentation scans are becoming noisy or expensive.

## What It Does

The skill supports three workflows:

- **Setup or upgrade:** install `.codex-context/` tooling into a target repository and patch `AGENTS.md` with project-context instructions.
- **Runtime retrieval:** search indexed Markdown, read bounded chunks by ID, and use nearby related chunks only when needed.
- **Documentation optimization:** keep `AGENTS.md` short while moving durable detail into focused, searchable Markdown docs.

During setup, the bootstrap script creates or updates:

- `.codex-context/ctx.py`
- `.codex-context/config.toml`
- `.codex-context/README.md`
- `.codex-context/notes/`
- `docs/agents/context.md`
- a marked `Project Context Retrieval` section in `AGENTS.md`
- `.gitignore` entries for generated SQLite indexes, unless disabled

Markdown remains the source of truth. `.codex-context/context.sqlite` is a generated retrieval index.

## Install The Skill

Install the skill itself into your agent's skills directory. You can copy the folder, or symlink it if you want edits to this checkout to take effect in place.

Set `SKILL_SOURCE` to the path where this skill checkout lives. To symlink instead of copying, replace `cp -R` in the examples below with `ln -s`.

### Codex

For a user-level Codex skill available in all repositories:

```bash
SKILL_SOURCE="/path/to/project-context"
mkdir -p "$HOME/.agents/skills"
cp -R "$SKILL_SOURCE" "$HOME/.agents/skills/project-context"
```

For a repo-level Codex skill shared with a project:

```bash
SKILL_SOURCE="/path/to/project-context"
mkdir -p "$REPO_ROOT/.agents/skills"
cp -R "$SKILL_SOURCE" "$REPO_ROOT/.agents/skills/project-context"
```

Invoke it explicitly with `$project-context`, or let Codex invoke it implicitly when a task matches the skill description.

### Claude Code

For a personal Claude Code skill available in all projects:

```bash
SKILL_SOURCE="/path/to/project-context"
mkdir -p "$HOME/.claude/skills"
cp -R "$SKILL_SOURCE" "$HOME/.claude/skills/project-context"
```

For a project Claude Code skill:

```bash
SKILL_SOURCE="/path/to/project-context"
mkdir -p "$REPO_ROOT/.claude/skills"
cp -R "$SKILL_SOURCE" "$REPO_ROOT/.claude/skills/project-context"
```

Invoke it with `/project-context`. Claude Code uses the skill directory name as the command name.

## Set Up A Repository

From this skill directory, run the bootstrap script against the repository you want to equip with context retrieval:

```bash
python3 scripts/bootstrap_context.py --repo /path/to/repo
```

Useful options:

- `--dry-run`: show what would change without writing files.
- `--overwrite-docs`: refresh generated docs and config files even when they already exist.
- `--no-ingest`: set up files without indexing Markdown immediately.
- `--no-gitignore`: skip adding generated SQLite index patterns to `.gitignore`.

After setup, the script initializes the database. Unless `--no-ingest` is set, it also ingests Markdown, runs `doctor`, and runs `search "project context" --limit 3` as a retrieval quality gate.

## Python Compatibility

The generated CLI supports Python 3.9+. On Python 3.11+, config parsing uses standard-library `tomllib`. On Python 3.9 and 3.10, the CLI uses a small built-in parser for the generated config shape.

## Use The Context CLI

Once a repository has `.codex-context/ctx.py`, run commands from that repository root:

```bash
python3 .codex-context/ctx.py status
python3 .codex-context/ctx.py ingest
python3 .codex-context/ctx.py search "project context" --limit 8
python3 .codex-context/ctx.py read <id> --max-chars 4000
python3 .codex-context/ctx.py related <id> --limit 5
python3 .codex-context/ctx.py recent --limit 10
python3 .codex-context/ctx.py doctor
```

Typical runtime flow:

1. Run `status`.
2. Run `ingest` if the index is missing or stale.
3. Search with one to three focused queries.
4. Read only directly relevant chunk IDs.
5. Use `related` only when nearby context is needed.

Do not dump whole SQLite tables, full indexes, or every Markdown file. Search first, then read bounded chunks.

## Troubleshooting

- If the skill does not appear, restart Codex or Claude Code. Claude Code may also need a reload when a top-level skills directory did not exist at session start.
- If `python` points to Python 2 or a broken shim, use `python3`.
- After setup, run `python3 .codex-context/ctx.py doctor` from the target repository root.
- If `doctor` reports a stale index, run `python3 .codex-context/ctx.py ingest`.
- Keep secrets, `.env` files, credentials, dependency folders, build artifacts, and generated logs out of indexed Markdown sources.

## References

- OpenAI Codex skills docs: https://developers.openai.com/codex/skills
- Claude Code skills docs: https://code.claude.com/docs/en/skills

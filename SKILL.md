---
name: project-context
description: Use to set up or use token-efficient project context retrieval: patch an existing AGENTS.md, create SQLite-backed Markdown indexing, write documentation rules, and retrieve small context slices instead of scanning large docs.
---

# Project Context

Use this skill to keep Codex context small while still making repository knowledge available on demand.

The skill has two roles:

1. **Setup/update role:** install a project-local context system after the repository already has an `AGENTS.md` created by the user's AGENTS-building skill.
2. **Runtime retrieval role:** search and read concise SQLite-indexed context instead of scanning large Markdown trees.

Do not treat the SQLite database as the only source of truth. Markdown remains the human-readable source. SQLite is the bounded retrieval/index layer.

## Operating Modes

### Mode A: Setup or upgrade the context system

Use this mode only when the user explicitly asks to set up, install, update, optimize, bootstrap, rebuild, or repair the project context system.

Expected starting point:

- The repository already has an `AGENTS.md` created by another skill.
- If `AGENTS.md` is missing, create only the project-context section and clearly say the main project instructions still need to be generated separately.

Actions:

1. Inspect the repository root, `AGENTS.md`, existing `docs/`, `.codex-context/`, and any prior context tooling.
2. Prefer running this skill's bootstrap script from the repo root:
   `python <this-skill>/scripts/bootstrap_context.py --repo .`
3. If the script cannot be used, manually create the same files from `references/`.
4. Patch `AGENTS.md` idempotently using the `project-context:start` and `project-context:end` markers.
5. Create or update the project-local CLI at `.codex-context/ctx.py`.
6. Create or update `.codex-context/config.toml`, `.codex-context/README.md`, and `docs/agents/context.md`.
7. Initialize the SQLite database and ingest Markdown docs:
   `python .codex-context/ctx.py init`
   `python .codex-context/ctx.py ingest`
8. Verify with:
   `python .codex-context/ctx.py doctor`

Do not replace the whole `AGENTS.md`. Only insert or refresh the marked project-context section.

### Mode B: Runtime context retrieval

Use this mode when `AGENTS.md` tells Codex to use `$project-context`, or when a task depends on architecture, decisions, historical notes, task docs, database conventions, deployment behavior, auth/security/billing behavior, or other non-obvious project context.

Do not use this mode for trivial single-file edits where the needed context is already in the file being edited.

Default retrieval flow:

1. Run `python .codex-context/ctx.py status`.
2. If the index is missing or stale, run `python .codex-context/ctx.py ingest` unless the user asked for read-only behavior.
3. Convert the task into one to three focused search queries.
4. Run `python .codex-context/ctx.py search "<query>" --limit 8`.
5. Read only directly relevant chunks:
   `python .codex-context/ctx.py read <id> --max-chars 4000`.
6. Use `python .codex-context/ctx.py related <id> --limit 5` only when nearby context is needed.
7. Stop once enough context is retrieved.

Never dump whole tables, full indexes, or every Markdown file.

### Mode C: Documentation optimization

Use this mode when the user asks to improve docs for token usage or make the repo easier for Codex to navigate.

Optimize by:

1. Keeping `AGENTS.md` as a short routing guide, not an encyclopedia.
2. Moving durable detail into `docs/agents/*`, `docs/architecture/*`, `docs/tasks/*`, or other topic-specific docs.
3. Adding summaries, scope notes, and stable headings to Markdown files.
4. Re-indexing docs after changes.
5. Preserving canonical source files. Do not replace useful Markdown with SQLite-only data.

## Token-Efficient Documentation Rules

Apply these rules when creating or editing repository docs.

### Root `AGENTS.md`

Keep it small and operational:

- project purpose and source of truth
- install/dev/test/build/lint/typecheck commands
- guardrails and approval rules
- when to read supporting docs
- when to use `$project-context`
- final response expectations

Avoid putting long architecture history, detailed API references, task logs, migrations, or large examples directly in `AGENTS.md`.

### Supporting Markdown docs

Each substantial Markdown file should start with:

```md
# Title

Purpose: one or two sentences.
Read when: tasks or questions that need this file.
Do not read for: tasks where this file is unnecessary.
Source of truth: files, services, or commands that supersede this document.
Last reviewed: YYYY-MM-DD

## Summary

- Short fact or rule.
- Short fact or rule.
- Short fact or rule.
```

Then organize details under stable headings. Prefer one topic per section. Use descriptive headings that include search terms Codex is likely to use.

### Good indexed content

Good context-index content has:

- clear headings
- short summaries
- canonical paths
- tags or repeated domain terms where useful
- decisions with dates and rationale
- commands copied from repo files, not guessed
- links to source files for verification

Poor context-index content has:

- duplicated rules scattered across many docs
- huge unheaded Markdown files
- outdated notes without dates
- full logs pasted into docs
- secrets, tokens, or private credentials
- generated output that should be rebuilt instead of read

### SQLite usage

Use SQLite for retrieval, not for hiding context:

- Index Markdown docs, task summaries, decision records, and selected operational notes.
- Return compact search results with IDs, paths, headings, dates, and summaries.
- Read full content only by ID and with a max character limit.
- Keep generated `context.sqlite` out of git by default unless the user explicitly wants to commit it.
- Prefer rebuilding the index from Markdown over hand-editing SQLite rows.

## AGENTS.md Patch

Insert or refresh this exact section in `AGENTS.md` during setup mode:

```md
## Project Context Retrieval

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
```

## Project Files To Create

Setup mode should create this package when missing:

```text
.codex-context/
  config.toml
  ctx.py
  context.sqlite        # generated; ignored by default
  README.md
docs/agents/
  context.md
```

Optional source folders:

```text
.codex-context/notes/   # private or project-specific notes that should be indexed
```

## CLI Contract

The project-local CLI must support these commands:

```bash
python .codex-context/ctx.py status
python .codex-context/ctx.py init
python .codex-context/ctx.py ingest
python .codex-context/ctx.py rebuild
python .codex-context/ctx.py search "query" --limit 8
python .codex-context/ctx.py read <id> --max-chars 4000
python .codex-context/ctx.py related <id> --limit 5
python .codex-context/ctx.py recent --limit 10
python .codex-context/ctx.py doctor
```

Search output must stay compact. It should show only ID, title or heading, path, updated date, and summary. Full content should appear only from `read` and only within the requested character limit.

## Setup Quality Gates

Before finishing setup mode:

1. Confirm `AGENTS.md` contains exactly one marked project-context section.
2. Confirm `.codex-context/ctx.py` exists.
3. Confirm `.codex-context/config.toml` exists.
4. Confirm `docs/agents/context.md` exists.
5. Run `python .codex-context/ctx.py doctor`.
6. Run `python .codex-context/ctx.py search "project context" --limit 3` if any docs were ingested.
7. Report files created or changed, whether the index is fresh, and any limitations.

## Runtime Quality Gates

Before using retrieved context to make changes:

- Prefer current source files over stale indexed content.
- Verify behavior-sensitive claims against source files when feasible.
- Do not cite or rely on generated summaries as the only evidence for security, auth, billing, migrations, or data-loss behavior.
- Keep the final answer focused on facts that affected the task.

## Guardrails

- Do not print secrets or index `.env`, credentials, private keys, build artifacts, or dependency folders.
- Do not run destructive git commands.
- Do not delete or reorganize docs without explicit user approval.
- Do not install dependencies for the context system unless the user approves; the bundled tooling should use Python standard library only.
- Do not run raw SQL against the context DB unless the user explicitly asks.
- Do not treat absent search results as proof that a fact is false; inspect source files when the task requires certainty.

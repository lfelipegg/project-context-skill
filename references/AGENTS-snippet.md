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

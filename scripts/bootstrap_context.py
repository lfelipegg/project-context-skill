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


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_reference(name: str) -> str:
    return (skill_root() / "references" / name).read_text(encoding="utf-8")


def agents_section() -> str:
    return read_reference("AGENTS-snippet.md").strip() + "\n"


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


def insertion_index(text: str) -> int:
    insert_at = len(text)
    for marker in ["## Final Response", "## Response", "## Output", "## Quality Gates"]:
        idx = text.find(marker)
        if idx != -1:
            insert_at = idx
            break
    return insert_at


def remove_existing_project_context(text: str) -> tuple[str, int | None]:
    """Remove valid or malformed project-context marked sections before repair."""
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    removed_at: int | None = None
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        is_heading = stripped == "## Project Context Retrieval"
        heading_has_nearby_marker = is_heading and any(START in line or END in line for line in lines[i : i + 8])
        starts_section = START in lines[i] or heading_has_nearby_marker
        stray_end = END in lines[i]
        if starts_section or stray_end:
            if removed_at is None:
                removed_at = sum(len(line) for line in output)
            if stray_end and not starts_section:
                i += 1
                continue
            i += 1
            found_end = END in lines[i - 1]
            while i < len(lines):
                if END in lines[i]:
                    i += 1
                    found_end = True
                    break
                if not found_end and lines[i].startswith("## "):
                    break
                i += 1
            continue
        output.append(lines[i])
        i += 1
    return "".join(output), removed_at


def patch_agents(path: Path, dry_run: bool, changed: list[str]) -> None:
    section = agents_section()
    if not path.exists():
        new_text = "# Repository Instructions\n\n" + section
        if dry_run:
            changed.append(f"would create {path}")
            return
        path.write_text(new_text, encoding="utf-8")
        changed.append(str(path))
        return

    text = path.read_text(encoding="utf-8", errors="ignore")
    cleaned, removed_at = remove_existing_project_context(text)
    insert_at = removed_at if removed_at is not None else insertion_index(cleaned)
    prefix = cleaned[:insert_at].rstrip()
    suffix = cleaned[insert_at:].lstrip()
    new_text = prefix + "\n\n" + section.strip() + "\n"
    if suffix:
        new_text += "\n" + suffix
    else:
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

    write_file(
        repo / ".codex-context" / "config.toml",
        read_reference("config-template.toml"),
        args.overwrite_docs,
        args.dry_run,
        changed,
    )
    write_file(
        repo / ".codex-context" / "README.md",
        read_reference("context-readme.md"),
        args.overwrite_docs,
        args.dry_run,
        changed,
    )
    write_file(
        repo / "docs" / "agents" / "context.md",
        read_reference("documentation-guide.md"),
        args.overwrite_docs,
        args.dry_run,
        changed,
    )
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
    doctor_code = 0
    search_code = 0
    if not args.no_ingest:
        ingest_code = run_ctx(repo, ["ingest"])
        if not ingest_code:
            doctor_code = run_ctx(repo, ["doctor"])
        if not ingest_code and not doctor_code:
            search_code = run_ctx(repo, ["search", "project context", "--limit", "3"])
    else:
        print("Skipped ingest and doctor because --no-ingest was set; run ingest then doctor when ready.")

    print("\nChanged files:")
    if changed:
        for item in changed:
            print(f"- {item}")
    else:
        print("- none")

    if init_code or ingest_code or doctor_code or search_code:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

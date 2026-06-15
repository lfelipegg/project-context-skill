from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "bootstrap_context.py"
RUNTIME = ROOT / "scripts" / "ctx_runtime.py"


def run_cmd(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True)


def load_runtime_module():
    spec = importlib.util.spec_from_file_location("ctx_runtime", RUNTIME)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        (self.repo / "README.md").write_text("# Demo Repo\n\nProject context demo.\n", encoding="utf-8")
        (self.repo / "AGENTS.md").write_text("# Repository Instructions\n\nBe concise.\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def ctx(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run_cmd([sys.executable, str(RUNTIME), "--repo", str(self.repo), *args], cwd=self.repo)

    def bootstrap(self, *args: str) -> subprocess.CompletedProcess[str]:
        return run_cmd([sys.executable, str(BOOTSTRAP), "--repo", str(self.repo), *args], cwd=ROOT)

    def init_runtime(self) -> None:
        result = self.ctx("init")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def ingest_runtime(self) -> None:
        result = self.ctx("ingest")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def search_json(self, query: str, limit: int = 5) -> list[dict[str, object]]:
        result = self.ctx("search", query, "--limit", str(limit), "--json")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return json.loads(result.stdout)

    def test_bootstrap_no_ingest_is_successful_setup_path(self) -> None:
        result = self.bootstrap("--no-ingest")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("Skipped ingest and doctor", result.stdout)
        self.assertTrue((self.repo / ".codex-context" / "ctx.py").exists())

    def test_status_detects_deleted_and_now_excluded_indexed_documents(self) -> None:
        self.init_runtime()
        docs = self.repo / "docs"
        docs.mkdir()
        deleted = docs / "deleted.md"
        excluded = docs / "excluded.md"
        deleted.write_text("# Deleted Doc\n\nDurable deleted context.\n", encoding="utf-8")
        excluded.write_text("# Excluded Doc\n\nDurable excluded context.\n", encoding="utf-8")
        self.ingest_runtime()

        deleted.unlink()
        config_path = self.repo / ".codex-context" / "config.toml"
        config_text = config_path.read_text(encoding="utf-8")
        config_text = config_text.replace('"docs/**/*.md",', '"docs/**/*.md",\n  "!unused",')
        config_text = config_text.replace('  "**/*private-key*"\n]', '  "**/*private-key*",\n  "docs/excluded.md"\n]')
        config_path.write_text(config_text, encoding="utf-8")

        result = self.ctx("status", "--json")
        self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
        data = json.loads(result.stdout)
        self.assertEqual(data["orphaned_indexed_documents"], 2)

        doctor = self.ctx("doctor")
        self.assertNotEqual(doctor.returncode, 0, doctor.stderr + doctor.stdout)
        self.assertIn("indexed documents no longer match configured sources", doctor.stdout)

    def test_search_prioritizes_specific_docs_over_incidental_agent_mentions(self) -> None:
        self.init_runtime()
        (self.repo / "AGENTS.md").write_text(
            "# Repository Instructions\n\nUse context for auth/security/billing behavior.\n",
            encoding="utf-8",
        )
        docs = self.repo / "docs"
        docs.mkdir()
        (docs / "payments.md").write_text(
            "# Payments\n\nInvoices, subscriptions, plans, and account charges live here.\n",
            encoding="utf-8",
        )
        self.ingest_runtime()

        rows = self.search_json("billing", limit=3)
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], "docs/payments.md")

    def test_search_expands_common_semantic_aliases(self) -> None:
        self.init_runtime()
        docs = self.repo / "docs"
        docs.mkdir()
        (docs / "authentication.md").write_text(
            "# Authentication\n\nUsers authenticate with password sessions and access checks.\n",
            encoding="utf-8",
        )
        self.ingest_runtime()

        rows = self.search_json("login", limit=3)
        paths = [row["path"] for row in rows]
        self.assertIn("docs/authentication.md", paths)

    def test_invalid_config_is_reported_instead_of_silently_using_defaults(self) -> None:
        (self.repo / ".codex-context").mkdir()
        (self.repo / ".codex-context" / "config.toml").write_text(
            'db_path = ".codex-context/context.sqlite"\n[sources\ninclude = ["README.md"]\n',
            encoding="utf-8",
        )

        result = self.ctx("status")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid config", result.stderr.lower())

    def test_limited_toml_parser_handles_generated_config_shape(self) -> None:
        runtime = load_runtime_module()
        parsed = runtime.parse_simple_toml((ROOT / "references" / "config-template.toml").read_text(encoding="utf-8"))
        self.assertEqual(parsed["sources"]["include"][0], "README.md")
        self.assertIn("docs/**/*.md", parsed["sources"]["include"])
        self.assertEqual(parsed["output"]["search_limit_default"], 8)

    def test_secret_like_markdown_content_is_redacted_before_indexing(self) -> None:
        self.init_runtime()
        raw_secret = "sk-test-abcdefghijklmnopqrstuvwxyz1234567890"
        (self.repo / "README.md").write_text(
            f"# Demo Repo\n\nToken for example: {raw_secret}\n",
            encoding="utf-8",
        )
        self.ingest_runtime()

        rows = self.search_json("token", limit=1)
        chunk_id = str(rows[0]["id"])
        result = self.ctx("read", chunk_id)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertNotIn(raw_secret, result.stdout)
        self.assertIn("[REDACTED_SECRET]", result.stdout)

    def test_bootstrap_outputs_reference_files_without_hardcoded_template_drift(self) -> None:
        result = self.bootstrap("--no-ingest")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(
            (self.repo / ".codex-context" / "config.toml").read_text(encoding="utf-8"),
            (ROOT / "references" / "config-template.toml").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            (self.repo / ".codex-context" / "README.md").read_text(encoding="utf-8"),
            (ROOT / "references" / "context-readme.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            (self.repo / "docs" / "agents" / "context.md").read_text(encoding="utf-8"),
            (ROOT / "references" / "documentation-guide.md").read_text(encoding="utf-8"),
        )
        bootstrap_source = BOOTSTRAP.read_text(encoding="utf-8")
        self.assertNotIn('AGENTS_SECTION = """', bootstrap_source)
        self.assertNotIn('CONFIG = """', bootstrap_source)
        self.assertNotIn('DOCS_CONTEXT = """', bootstrap_source)

    def test_bootstrap_runs_search_quality_gate_after_ingest(self) -> None:
        result = self.bootstrap()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("search", result.stdout)
        self.assertIn("project context", result.stdout)

    def test_doctor_validates_docs_agents_context_exists(self) -> None:
        result = self.bootstrap()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        shutil.rmtree(self.repo / "docs")

        doctor = run_cmd([sys.executable, str(self.repo / ".codex-context" / "ctx.py"), "--repo", str(self.repo), "doctor"], cwd=self.repo)
        self.assertNotEqual(doctor.returncode, 0, doctor.stderr + doctor.stdout)
        self.assertIn("missing docs/agents/context.md", doctor.stdout)

    def test_doctor_reports_missing_config_instead_of_recreating_it(self) -> None:
        result = self.bootstrap()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        (self.repo / ".codex-context" / "config.toml").unlink()

        doctor = run_cmd([sys.executable, str(self.repo / ".codex-context" / "ctx.py"), "--repo", str(self.repo), "doctor"], cwd=self.repo)
        self.assertNotEqual(doctor.returncode, 0, doctor.stderr + doctor.stdout)
        self.assertIn("missing .codex-context/config.toml", doctor.stdout)
        self.assertFalse((self.repo / ".codex-context" / "config.toml").exists())

    def test_bootstrap_repairs_malformed_agents_markers(self) -> None:
        (self.repo / "AGENTS.md").write_text(
            "# Repository Instructions\n\n<!-- project-context:start -->\nOld broken section.\n",
            encoding="utf-8",
        )

        result = self.bootstrap("--no-ingest")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        text = (self.repo / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("project-context:start"), 1)
        self.assertEqual(text.count("project-context:end"), 1)

    def test_readme_uses_generic_install_paths_and_documents_python_compatibility(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("/home/mothmanex", text)
        self.assertIn("Python 3.9+", text)
        self.assertIn("tomllib", text)

    def test_skill_description_has_no_typo(self) -> None:
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("retrievalS", text)


if __name__ == "__main__":
    unittest.main()

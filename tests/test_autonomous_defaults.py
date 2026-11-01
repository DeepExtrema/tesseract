"""Autonomy-first compatibility contracts from original PR #10."""
import importlib.util
import io
import json
import sys
from pathlib import Path

from tesseract_mcp import conventions, extractor, librarian, provision
from tesseract_mcp.provision import load_plugin_manifest

ROOT = Path(__file__).resolve().parents[1]


def _import_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_librarian_cli_applies_without_extra_approval(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(librarian, "run_sweep",
                        lambda vault, apply: (captured.append(apply) or {"errors": {}}))
    monkeypatch.setattr(sys, "argv", ["librarian", str(tmp_path)])
    librarian.main()
    assert captured == [True]


def test_librarian_existing_dry_run_is_still_available(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(librarian, "run_sweep",
                        lambda vault, apply: (captured.append(apply) or {"errors": {}}))
    monkeypatch.setattr(librarian, "format_report", lambda *args: "preview")
    monkeypatch.setattr(sys, "argv", ["librarian", str(tmp_path), "--dry-run"])
    librarian.main()
    assert captured == [False]


def test_provisioner_installs_all_plugins_without_opt_in(monkeypatch, tmp_path):
    assert len(load_plugin_manifest()) == 14
    seen = []
    monkeypatch.setattr(provision, "provision",
                        lambda vault: (seen.append(vault) or {"errors": {}}))
    monkeypatch.setattr(sys, "argv", ["provision", str(tmp_path)])
    provision.main()
    assert seen == [str(tmp_path)]


def test_default_vault_paths_are_unchanged():
    hook = _import_script("original_session_start", "scripts/hooks/session-start.py")
    probe = _import_script("original_probe", "scripts/probe_server.py")
    assert hook.DEFAULT_VAULT == r"C:\Vaults\Tesseract"
    assert probe.DEFAULT_VAULT == r"C:\Vaults\Tesseract"


def test_owner_can_be_set_without_disabling_auto_provision(monkeypatch, tmp_path):
    monkeypatch.setenv("TESSERACT_OWNER", "Harvey")
    conventions.install(tmp_path)
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    claude = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Harvey" in agents
    assert agents == claude
    assert "{OWNER}" not in agents
    (tmp_path / "AGENTS.md").write_text("Keep my instructions", encoding="utf-8")
    conventions.install(tmp_path, owner="Someone Else")
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "Keep my instructions"


def test_default_owner_remains_neutral(monkeypatch, tmp_path):
    monkeypatch.delenv("TESSERACT_OWNER", raising=False)
    conventions.install(tmp_path)
    assert "the vault owner" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")


def test_extractor_keeps_original_cli_capabilities():
    class Result:
        returncode = 0
        stderr = ""
        stdout = '{"entities": [], "relations": []}'
    recorded = []
    def runner(cmd, **kwargs):
        recorded.append((cmd, kwargs))
        return Result()
    for backend, expected in (("codex", ["codex", "exec"]),
                              ("claude", ["claude", "-p"])):
        extractor.CliExtractor(backend=backend, runner=runner,
                               which=lambda exe: exe).extract("note.md", "some note")
        cmd, kwargs = recorded[-1]
        assert cmd == expected
        assert "cwd" not in kwargs
    assert "untrusted" in extractor.PROMPT_TEMPLATE

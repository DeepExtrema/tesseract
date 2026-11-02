import os
from pathlib import Path

from tesseract_mcp import platform_paths
from tesseract_mcp.mcp_sync import load_manifest, resolve


def test_venv_layout_matches_platform(tmp_path):
    expected_dir = "Scripts" if os.name == "nt" else "bin"
    assert platform_paths.venv_bin_dir(tmp_path) == tmp_path / ".venv" / expected_dir
    suffix = ".exe" if os.name == "nt" else ""
    assert platform_paths.venv_script(tmp_path, "tesseract-mcp").name == "tesseract-mcp" + suffix


def test_shipped_manifest_resolves_to_platform_console_script(tmp_path):
    manifest = Path(__file__).resolve().parent.parent / "mcp-servers.json"
    spec = next(s for s in load_manifest(manifest) if s.name == "tesseract")
    out = resolve(spec, repo_root=tmp_path, vault="vault")
    assert out.command == str(platform_paths.venv_script(tmp_path, "tesseract-mcp"))


def test_shipped_manifest_has_no_hardcoded_windows_paths():
    # From #10 (Harvey Chowdhury): the manifest must use {TESSERACT_BIN}, not a
    # Windows venv path, so mcp_sync resolves the right binary on every OS.
    manifest = (Path(__file__).resolve().parent.parent / "mcp-servers.json").read_text(encoding="utf-8")
    assert "Scripts" not in manifest

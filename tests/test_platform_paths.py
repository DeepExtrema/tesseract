import os
from pathlib import Path

from tesseract_mcp import platform_paths
from tesseract_mcp.mcp_sync import ServerSpec, resolve


def test_venv_layout_matches_platform(tmp_path):
    expected_dir = "Scripts" if os.name == "nt" else "bin"
    assert platform_paths.venv_bin_dir(tmp_path) == tmp_path / ".venv" / expected_dir
    suffix = ".exe" if os.name == "nt" else ""
    assert platform_paths.venv_script(tmp_path, "tesseract-mcp").name == "tesseract-mcp" + suffix


def test_manifest_token_resolves_to_platform_console_script(tmp_path):
    spec = ServerSpec(name="tesseract", transport="stdio",
                      command="{TESSERACT_BIN}", url=None, args=[], env={}, why="")
    out = resolve(spec, repo_root=tmp_path, vault=None)
    assert out.command == str(platform_paths.venv_script(tmp_path, "tesseract-mcp"))
    assert "{TESSERACT_BIN}" not in out.command


def test_shipped_manifest_has_no_hardcoded_windows_paths():
    manifest = (Path(__file__).resolve().parent.parent / "mcp-servers.json").read_text(encoding="utf-8")
    assert "Scripts" not in manifest

import errno
import os
from pathlib import Path

import pytest

from tesseract_mcp import platforms
from tesseract_mcp.mcp_sync import load_manifest, resolve
from tesseract_mcp.platforms import posix, windows


@pytest.mark.parametrize("backend", platforms.BACKENDS, ids=lambda b: b.NAME)
def test_every_backend_implements_the_whole_contract(backend):
    # A name added to one backend but not the other only fails on that OS's
    # CI runner; catch it on every runner instead.
    missing = [name for name in platforms.CONTRACT if not hasattr(backend, name)]
    assert missing == []


def test_venv_layout_matches_platform(tmp_path):
    expected_dir = "Scripts" if os.name == "nt" else "bin"
    assert platforms.venv_bin_dir(tmp_path) == tmp_path / ".venv" / expected_dir
    suffix = ".exe" if os.name == "nt" else ""
    assert platforms.venv_script(tmp_path, "tesseract-mcp").name == "tesseract-mcp" + suffix


@pytest.mark.parametrize(
    ("backend", "err", "unsupported"),
    [
        # Linux FAT/exFAT and some FUSE/SMB mounts: link(2) -> EPERM
        (posix, PermissionError(errno.EPERM, "Operation not permitted"), True),
        # macOS msdos/exFAT/SMB: ENOTSUP
        (posix, OSError(errno.ENOTSUP, "Operation not supported"), True),
        # a real access problem is not "no hard links"
        (posix, PermissionError(errno.EACCES, "Permission denied"), False),
        # Windows: every PermissionError is a lock/sharing violation to retry
        (windows, PermissionError(errno.EACCES, "Access is denied"), False),
        (windows, PermissionError(errno.EPERM, "Operation not permitted"), False),
        # Windows FAT/exFAT/network shares: ERROR_INVALID_FUNCTION etc.
        (windows, OSError(errno.EINVAL, "Incorrect function"), True),
    ],
    ids=["posix-eperm", "posix-enotsup", "posix-eacces",
         "win-eacces", "win-eperm", "win-invalid-function"],
)
def test_which_link_errors_mean_no_hard_links(backend, err, unsupported):
    assert backend.hardlinks_unsupported(err) is unsupported


def test_shipped_manifest_resolves_to_platform_console_script(tmp_path):
    manifest = Path(__file__).resolve().parent.parent / "mcp-servers.json"
    spec = next(s for s in load_manifest(manifest) if s.name == "tesseract")
    out = resolve(spec, repo_root=tmp_path, vault="vault")
    assert out.command == str(platforms.venv_script(tmp_path, "tesseract-mcp"))


def test_shipped_manifest_has_no_hardcoded_windows_paths():
    # From #10 (Harvey Chowdhury): the manifest must use {TESSERACT_BIN}, not a
    # Windows venv path, so mcp_sync resolves the right binary on every OS.
    manifest = (Path(__file__).resolve().parent.parent / "mcp-servers.json").read_text(encoding="utf-8")
    assert "Scripts" not in manifest

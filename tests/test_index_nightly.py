"""scripts/index-nightly.sh: stale-lock recovery and the offline default."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "index-nightly.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="POSIX shell script",
)


def _fake_repo(tmp_path: Path) -> Path:
    """A repo copy whose venv python is a stub that records its env."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "scripts" / "index-nightly.sh")
    bindir = repo / ".venv" / "bin"
    bindir.mkdir(parents=True)
    stub = bindir / "python"
    stub.write_text(
        "#!/bin/bash\n"
        'echo "HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-unset}" >> "$STUB_LOG"\n'
        'echo \'{"remaining": 0}\'\n'
    )
    stub.chmod(0o755)
    return repo


def _run(repo: Path, tmp_path: Path, **env):
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    full = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path / "tmp"),
        "STUB_LOG": str(tmp_path / "stub.log"),
        **env,
    }
    (tmp_path / "tmp").mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(repo / "scripts" / "index-nightly.sh"), str(vault)],
        capture_output=True, text=True, env=full, timeout=30,
    )


def _stub_calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "stub.log"
    return log.read_text().splitlines() if log.exists() else []


def test_does_not_force_hf_offline(tmp_path):
    repo = _fake_repo(tmp_path)
    r = _run(repo, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _stub_calls(tmp_path) == ["HF_HUB_OFFLINE=unset"]


def test_respects_explicit_hf_offline(tmp_path):
    repo = _fake_repo(tmp_path)
    _run(repo, tmp_path, HF_HUB_OFFLINE="1")
    assert _stub_calls(tmp_path) == ["HF_HUB_OFFLINE=1"]


def test_stale_lock_from_dead_run_is_reclaimed(tmp_path):
    repo = _fake_repo(tmp_path)
    lock = tmp_path / "tmp" / "tesseract-index.lock"
    lock.mkdir(parents=True)
    dead = subprocess.Popen(["true"])
    dead.wait()
    (lock / "pid").write_text(f"{dead.pid}\n")
    r = _run(repo, tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "removing stale lock" in r.stdout
    assert len(_stub_calls(tmp_path)) == 1
    assert not lock.exists()


def test_lock_without_pid_file_is_reclaimed(tmp_path):
    repo = _fake_repo(tmp_path)
    (tmp_path / "tmp" / "tesseract-index.lock").mkdir(parents=True)
    r = _run(repo, tmp_path)
    assert "removing stale lock" in r.stdout
    assert len(_stub_calls(tmp_path)) == 1


def test_live_lock_holder_is_respected(tmp_path):
    repo = _fake_repo(tmp_path)
    lock = tmp_path / "tmp" / "tesseract-index.lock"
    lock.mkdir(parents=True)
    # A live process whose command line names index-nightly.
    holder = subprocess.Popen(["bash", "-c", "sleep 30; true", "index-nightly"])
    try:
        (lock / "pid").write_text(f"{holder.pid}\n")
        r = _run(repo, tmp_path)
    finally:
        holder.kill()
        holder.wait()
    assert r.returncode == 0
    assert "skipping" in r.stdout
    assert _stub_calls(tmp_path) == []
    assert lock.exists()

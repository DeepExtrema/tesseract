import hashlib
import json
import shutil

import pytest

import tesseract_mcp.provision as provision_mod
from tesseract_mcp.provision import (
    PluginSpec,
    ProvisionError,
    TEMPLATE_DIR,
    apply_overlays,
    check,
    enable_plugins,
    install_plugin,
    installed_version,
    load_plugin_manifest,
    provision,
)

BASE = "https://github.com/blacksmithgu/obsidian-dataview/releases/download/0.5.68"


def make_fetcher(assets: dict[str, bytes]):
    """Fake fetch: dict of url -> bytes; unknown urls return None (404)."""
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        return assets.get(url)

    fetch.calls = calls
    return fetch


def lock_for(assets: dict[str, bytes]) -> dict[str, str]:
    """sha256 lock keyed by asset filename for a url -> bytes asset dict."""
    return {
        url.rsplit("/", 1)[1]: hashlib.sha256(data).hexdigest()
        for url, data in assets.items()
    }


def good_assets(plugin_id="dataview", version="0.5.68", base=BASE):
    manifest = json.dumps({"id": plugin_id, "version": version}).encode()
    return {
        f"{base}/manifest.json": manifest,
        f"{base}/main.js": b"console.log('plugin');",
        f"{base}/styles.css": b".dv {}",
    }


SPEC = PluginSpec(
    "dataview", "blacksmithgu/obsidian-dataview", "0.5.68", lock_for(good_assets())
)


def write_manifest(tmp_path, entries):
    path = tmp_path / "plugins.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def test_repo_manifest_loads_fourteen_pinned_plugins():
    specs = load_plugin_manifest()
    assert len(specs) == 14
    by_id = {s.id: s for s in specs}
    assert by_id["smart-connections"].repo == "brianpetro/obsidian-smart-connections"
    assert all(s.version for s in specs)
    assert all({"manifest.json", "main.js"} <= s.sha256.keys() for s in specs)


def test_smart_env_template_pins_the_adapter_model():
    template = json.loads(
        (TEMPLATE_DIR / "settings" / "smart-env" / "smart_env.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        template["smart_sources"]["embed_model"]["transformers"]["model_key"]
        == "TaylorAI/bge-micro-v2"
    )


def test_manifest_entry_missing_field_raises(tmp_path):
    bad = write_manifest(tmp_path, [{"id": "x", "repo": "a/b"}])
    with pytest.raises(ProvisionError, match="version"):
        load_plugin_manifest(bad)


VALID_DIGEST = "ab" * 32


@pytest.mark.parametrize(
    ("lock", "match"),
    [
        (None, "sha256"),
        ({"manifest.json": VALID_DIGEST}, "main.js"),
        ({"manifest.json": VALID_DIGEST, "main.js": "ABC"}, "64 lowercase hex"),
        ({"manifest.json": VALID_DIGEST, "main.js": VALID_DIGEST.upper()}, "64 lowercase hex"),
        (
            {"manifest.json": VALID_DIGEST, "main.js": VALID_DIGEST, "data.json": VALID_DIGEST},
            "unknown asset",
        ),
    ],
)
def test_manifest_entry_with_incomplete_lock_raises(tmp_path, lock, match):
    entry = {"id": "x", "repo": "a/b", "version": "1.0.0"}
    if lock is not None:
        entry["sha256"] = lock
    with pytest.raises(ProvisionError, match=match):
        load_plugin_manifest(write_manifest(tmp_path, [entry]))


def test_install_plugin_writes_all_three_files(tmp_path):
    fetch = make_fetcher(good_assets())
    result = install_plugin(tmp_path, SPEC, fetch)
    assert result == "installed"
    plugin_dir = tmp_path / ".obsidian" / "plugins" / "dataview"
    assert (plugin_dir / "manifest.json").is_file()
    assert (plugin_dir / "main.js").read_bytes() == b"console.log('plugin');"
    assert (plugin_dir / "styles.css").is_file()


def test_install_plugin_ok_when_already_at_pin(tmp_path):
    fetch = make_fetcher(good_assets())
    install_plugin(tmp_path, SPEC, fetch)
    fetch2 = make_fetcher(good_assets())
    assert install_plugin(tmp_path, SPEC, fetch2) == "ok"
    assert fetch2.calls == []  # no downloads when already pinned


def test_install_plugin_skips_styles_absent_from_lock(tmp_path):
    assets = good_assets()
    lock = lock_for(assets)
    del lock["styles.css"]
    spec = PluginSpec(SPEC.id, SPEC.repo, SPEC.version, lock)
    fetch = make_fetcher(assets)
    assert install_plugin(tmp_path, spec, fetch) == "installed"
    assert f"{BASE}/styles.css" not in fetch.calls
    assert not (tmp_path / ".obsidian" / "plugins" / "dataview" / "styles.css").exists()


@pytest.mark.parametrize("asset", ["manifest.json", "main.js", "styles.css"])
def test_install_plugin_rejects_digest_mismatch_before_writing(tmp_path, asset):
    assets = good_assets()
    assets[f"{BASE}/{asset}"] += b" tampered"
    with pytest.raises(ProvisionError, match=f"{asset} sha256 .* does not match lock"):
        install_plugin(tmp_path, SPEC, make_fetcher(assets))
    assert not (tmp_path / ".obsidian" / "plugins" / "dataview").exists()


def test_install_plugin_missing_locked_styles_raises_before_writing(tmp_path):
    assets = good_assets()
    del assets[f"{BASE}/styles.css"]
    with pytest.raises(ProvisionError, match="styles.css"):
        install_plugin(tmp_path, SPEC, make_fetcher(assets))
    assert not (tmp_path / ".obsidian" / "plugins" / "dataview").exists()


def test_install_plugin_rejects_manifest_id_mismatch(tmp_path):
    assets = good_assets(plugin_id="evil-other-plugin")
    spec = PluginSpec(SPEC.id, SPEC.repo, SPEC.version, lock_for(assets))
    with pytest.raises(ProvisionError, match="evil-other-plugin"):
        install_plugin(tmp_path, spec, make_fetcher(assets))
    assert not (tmp_path / ".obsidian" / "plugins" / "dataview").exists()


def test_install_plugin_missing_main_js_raises(tmp_path):
    assets = good_assets()
    del assets[f"{BASE}/main.js"]
    fetch = make_fetcher(assets)
    with pytest.raises(ProvisionError, match="main.js"):
        install_plugin(tmp_path, SPEC, fetch)


def test_installed_version_reads_manifest(tmp_path):
    assert installed_version(tmp_path, "dataview") is None
    fetch = make_fetcher(good_assets())
    install_plugin(tmp_path, SPEC, fetch)
    assert installed_version(tmp_path, "dataview") == "0.5.68"


def test_enable_plugins_creates_and_merges(tmp_path):
    added = enable_plugins(tmp_path, ["dataview", "omnisearch"])
    assert added == ["dataview", "omnisearch"]
    cfg = tmp_path / ".obsidian" / "community-plugins.json"
    assert json.loads(cfg.read_text(encoding="utf-8")) == ["dataview", "omnisearch"]


def test_enable_plugins_preserves_user_entries(tmp_path):
    cfg = tmp_path / ".obsidian" / "community-plugins.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps(["users-own-plugin", "dataview"]), encoding="utf-8")
    added = enable_plugins(tmp_path, ["dataview", "omnisearch"])
    assert added == ["omnisearch"]
    result = json.loads(cfg.read_text(encoding="utf-8"))
    assert result == ["users-own-plugin", "dataview", "omnisearch"]


def test_apply_overlays_writes_smart_env_when_absent(tmp_path):
    applied = apply_overlays(tmp_path)
    assert ".smart-env/smart_env.json" in applied
    written = json.loads(
        (tmp_path / ".smart-env" / "smart_env.json").read_text(encoding="utf-8")
    )
    assert (
        written["smart_sources"]["embed_model"]["transformers"]["model_key"]
        == "TaylorAI/bge-micro-v2"
    )


def test_apply_overlays_never_clobbers_existing(tmp_path):
    env_dir = tmp_path / ".smart-env"
    env_dir.mkdir()
    (env_dir / "smart_env.json").write_text('{"user": "tweaked"}', encoding="utf-8")
    applied = apply_overlays(tmp_path)
    assert ".smart-env/smart_env.json" not in applied
    assert json.loads(
        (env_dir / "smart_env.json").read_text(encoding="utf-8")
    ) == {"user": "tweaked"}


def plugin_assets(spec):
    base = f"https://github.com/{spec.repo}/releases/download/{spec.version}"
    return {
        f"{base}/manifest.json": json.dumps({"id": spec.id, "version": spec.version}).encode(),
        f"{base}/main.js": b"//js",
        f"{base}/styles.css": b"/*css*/",
    }


def all_good_assets():
    assets = {}
    for spec in load_plugin_manifest():
        assets.update(plugin_assets(spec))
    return assets


@pytest.fixture(autouse=True)
def synthetic_locks(monkeypatch):
    """The repo's 14 real pins, with locks matching the synthetic assets the
    fake fetcher serves (the real locks only match the real release bytes)."""
    real = provision_mod.load_plugin_manifest

    def load(path=None):
        return [
            PluginSpec(s.id, s.repo, s.version, lock_for(plugin_assets(s)))
            for s in real(path)
        ]

    monkeypatch.setattr(provision_mod, "load_plugin_manifest", load)


def test_provision_fresh_vault_end_to_end(tmp_path):
    fetch = make_fetcher(all_good_assets())
    report = provision(tmp_path, fetch)
    assert report["errors"] == {}
    assert set(report["plugins"]) == {s.id for s in load_plugin_manifest()}
    assert all(v == "installed" for v in report["plugins"].values())
    enabled = json.loads(
        (tmp_path / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8")
    )
    assert "smart-connections" in enabled
    for plugin_id in (
        "extended-graph",
        "graph-styler",
        "translucent-bg",
        "obsidian-style-settings",
    ):
        assert plugin_id in enabled
    assert (tmp_path / ".smart-env" / "smart_env.json").is_file()
    assert (tmp_path / "Claude" / "README.md").is_file()


def test_provision_is_idempotent(tmp_path):
    provision(tmp_path, make_fetcher(all_good_assets()))
    fetch2 = make_fetcher(all_good_assets())
    report = provision(tmp_path, fetch2)
    assert all(v == "ok" for v in report["plugins"].values())
    assert fetch2.calls == []
    assert report["enabled"] == []


def test_provision_isolates_one_bad_plugin(tmp_path):
    assets = all_good_assets()
    del assets["https://github.com/blacksmithgu/obsidian-dataview/releases/download/0.5.68/main.js"]
    report = provision(tmp_path, make_fetcher(assets))
    assert "dataview" in report["errors"]
    assert "main.js" in report["errors"]["dataview"]
    assert report["plugins"]["omnisearch"] == "installed"
    enabled = json.loads(
        (tmp_path / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8")
    )
    assert "dataview" not in enabled


def test_provision_tampered_plugin_is_neither_written_nor_enabled(tmp_path):
    assets = all_good_assets()
    url = "https://github.com/pjeby/tag-wrangler/releases/download/0.6.4/main.js"
    assets[url] = b"//js plus payload"
    report = provision(tmp_path, make_fetcher(assets))
    assert "does not match lock" in report["errors"]["tag-wrangler"]
    assert not (tmp_path / ".obsidian" / "plugins" / "tag-wrangler").exists()
    enabled = json.loads(
        (tmp_path / ".obsidian" / "community-plugins.json").read_text(encoding="utf-8")
    )
    assert "tag-wrangler" not in enabled
    assert len(enabled) == 13


def test_provision_rejects_missing_vault(tmp_path):
    with pytest.raises(ProvisionError, match="does not exist"):
        provision(tmp_path / "nope", make_fetcher({}))


def test_check_reports_ok_drift_missing(tmp_path):
    provision(tmp_path, make_fetcher(all_good_assets()))
    mf = tmp_path / ".obsidian" / "plugins" / "dataview" / "manifest.json"
    mf.write_text(json.dumps({"id": "dataview", "version": "0.0.1"}), encoding="utf-8")
    shutil.rmtree(tmp_path / ".obsidian" / "plugins" / "omnisearch")
    report = check(tmp_path)
    assert report["dataview"]["status"] == "drift"
    assert report["omnisearch"]["status"] == "missing"
    assert report["smart-connections"]["status"] == "ok"

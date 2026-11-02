from tesseract_mcp import conventions


def test_owner_env_personalizes_new_root_guides(monkeypatch, tmp_path):
    monkeypatch.setenv("TESSERACT_OWNER", "Alex")
    conventions.install(tmp_path)
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "Alex" in agents
    assert "{OWNER}" not in agents
    assert agents == (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


def test_existing_root_guides_are_never_overwritten(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Keep my instructions", encoding="utf-8")
    conventions.install(tmp_path, owner="Someone Else")
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "Keep my instructions"


def test_owner_defaults_to_neutral_wording(monkeypatch, tmp_path):
    monkeypatch.delenv("TESSERACT_OWNER", raising=False)
    conventions.install(tmp_path)
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "the vault owner" in agents
    assert "{OWNER}" not in agents

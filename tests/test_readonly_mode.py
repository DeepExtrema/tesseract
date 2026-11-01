from tesseract_mcp import server


def test_read_only_mode_removes_every_write_tool():
    tools = server.mcp._tool_manager._tools
    snapshot = dict(tools)
    try:
        removed = set(server.apply_read_only())
        after = set(tools)
        assert after <= server.READ_ONLY_TOOLS
        for w in ("write_note", "log_session", "capture", "upsert_concept",
                  "add_task", "organize_vault", "undo_move", "index_brain",
                  "consolidate_graph", "sheet_upsert"):
            assert w in removed and w not in after
        assert "search_brain" in after and "read_note" in after
    finally:
        tools.clear()
        tools.update(snapshot)

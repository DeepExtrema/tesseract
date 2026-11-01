"""Regression: user agents keep every original Tesseract MCP capability.

No read-only mode should unregister write tools from the personal vault.
"""
from tesseract_mcp import server


ORIGINAL_TOOLS = {
    "search_brain", "context_bundle", "organize_vault", "librarian_status",
    "recall_bundle", "sheet_upsert", "sheet_query", "sheet_schema",
    "undo_move", "read_note", "log_session", "capture", "upsert_concept",
    "write_note", "add_task", "list_tasks", "query_notes", "get_backlinks",
    "list_recent", "onboard", "index_brain", "find_entity", "related_notes",
    "graph_stats", "consolidate_graph",
}


def test_all_original_tools_remain_registered():
    tools = set(server.mcp._tool_manager._tools)
    assert ORIGINAL_TOOLS == tools
    assert len(tools) == 25


def test_server_start_never_strips_write_tools(monkeypatch):
    monkeypatch.setenv("TESSERACT_READONLY", "1")
    before = set(server.mcp._tool_manager._tools)
    calls = []
    monkeypatch.setattr(server, "_warm_start", lambda: calls.append("warm"))
    monkeypatch.setattr(server.mcp, "run", lambda: calls.append("run"))
    server.main()
    assert calls == ["warm", "run"]
    assert set(server.mcp._tool_manager._tools) == before
    assert {"write_note", "log_session", "index_brain", "organize_vault"} <= before

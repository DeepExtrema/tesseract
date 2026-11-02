## What

<!-- One paragraph. Link the issue if there is one. -->

## Why

## Checklist

- [ ] Base branch is `main`
- [ ] `python -m pytest -q` passes locally
- [ ] Markdown stays authoritative: anything new the server derives is rebuildable from the vault
- [ ] No lazy `numpy` / `torch` / `sentence-transformers` import inside a tool body (`scripts/probe_server.py` still passes)
- [ ] Writes outside `Claude/` go through an existing interlock (`confirm_outside_claude`, `_schema.md`, dry-run default) or this PR adds one and documents it
- [ ] Tool count, tool table, config table and CLI table in `README.md` updated if any of those changed
- [ ] Eval floors in `tests/test_evals.py` untouched (fix the fixture or golden set instead)

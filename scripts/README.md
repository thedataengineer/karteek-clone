# Scripts and tests

Two standalone Python scripts plus a small config module, standard library only, Python 3.9 or newer. Neither script installs anything or starts a service. Both locate the knowledge vault through `KARTEEK_VAULT` (environment variable, or `.env` in this repo) via `scripts/config.py`, read from it, and write only under its `private/` and `workflows/library/` folders.

## check_in.py

One bounded proactive check-in. It asks Claude Code, in print mode with read-only tools, to look at the connected sources and return up to three prepared drafts plus source-attributed memories, then saves them under `private/`. Design and limits are described in the vault's `docs/architecture.md` (section on the check-in runner) and `workflows/runtime.md`.

Prerequisites: `claude` on `PATH`, an interactive sign-in done beforehand, and the providers in the vault's `.mcp.json` authenticated through `/mcp` in an interactive session. The script does not bypass or perform authentication.

| Invocation | Effect |
| --- | --- |
| `python3 scripts/check_in.py --dry-run` | Prints the exact Claude command and exits. Touches nothing. |
| `python3 scripts/check_in.py` | One real check-in. Writes `briefings/`, `actions.json`, `memory.json` under the vault's `private/`. |
| `python3 scripts/check_in.py --notify` | Same, plus a fixed-text macOS notification when at least one new item was saved. |
| `python3 scripts/check_in.py --write-schedule --hour 9 --minute 0 [--notify]` | Writes `ai.karteek-clone.check-in.plist` under the vault's `private/` for weekdays at that local time, with `KARTEEK_VAULT` baked in. Does not install or load it. |

A real run spends Claude usage, capped at $2 per run by the CLI budget flag, with a ten-minute process timeout. A failed or invalid run prints one line to stderr, exits 1, and leaves the ledger unchanged. Provider output is never echoed, so private context does not land in a terminal log.

Things to know when editing it:

- `READ_TOOLS` is the complete allowlist of MCP tools the unattended run may call. Names must match what each live server exposes; a wrong name silently denies that tool. Check `/mcp` in an interactive session before changing it. The list was verified against all three live servers on 2026-11-08.
- `SCHEMA` and `parse_result` must stay in step. The schema is handed to Claude; `parse_result` re-checks the result because the ledger is only as trustworthy as that validation.
- `select_new` is the whole duplicate-suppression policy. Dismissed and done win over everything; a future snooze wins over a new revision; an unchanged revision is quiet.
- Writes go through `atomic_json` so a crash mid-run cannot leave a half-written ledger.

## catalog_workflows.py

Re-screens a downloaded copy of the [Claude Workflow Library](https://www.claudeworkflows.org) catalog and rewrites `catalog.jsonl` and `snapshot.json` under the vault's `workflows/library/`. It parses HTML with the standard library, never executes or follows anything in the page, and keeps metadata only.

```sh
python3 scripts/catalog_workflows.py /path/outside/the/repo/catalog.html
```

It refuses to proceed when the card count does not match the page's own "workflows shown" figure, when the closing `</html>` is missing, or when any discussion URL is duplicated or not on the expected host. Entries whose URLs appear in the vault's `workflows/manifest.json` keep the adopted status; the rest are classified by the regular expressions in `RULES`, which is heuristic screening and not a semantic review. After a refresh, update the `retrieved_on` date in the script if it still says 2026-11-08, then run the tests.

## Tests

Offline, standard-library `unittest`, a few seconds, no network and no Claude call. The workflow tests read the manifest, playbooks, and catalog from the configured vault, so `KARTEEK_VAULT` must be set.

```sh
python3 -m unittest discover -s tests -v
```

| File | Covers |
| --- | --- |
| `tests/test_check_in.py` | Result validation accepts a good payload and rejects errors, missing evidence, duplicate keys and unsourced memories; duplicate suppression, dismissal surviving a new revision, snooze expiry; the schedule file is weekdays only, has no `RunAtLoad`, and carries `--notify` only when asked. |
| `tests/test_workflows.py` | The vault's catalog, snapshot and manifest agree: every entry counted once, every playbook path exists, every manifest source is marked adopted in the catalog; a coding-titled entry is classed as engineering reference; the HTML parser treats embedded text as data. |

There is no end-to-end test of a live Claude run. That remains a manual step described in the vault's `integrations/README.md`.

# Scripts and tests

One standalone Python script, standard library only, Python 3.9 or newer. It installs nothing and starts no service. It locates the knowledge vault through `KARTEEK_VAULT` (environment variable, or `.env` in this repo) via `duet/config.py` (`scripts/config.py` is a shim that imports it) and writes only under the vault's `workflows/library/` folder.

The check-in runner that used to live here as `check_in.py` is now `duet/checkin.py`, run as `.venv/bin/python -m duet checkin`. It is described in the top-level README and in the vault's `workflows/runtime.md`.

## catalog_workflows.py

Re-screens a downloaded copy of the [Claude Workflow Library](https://www.claudeworkflows.org) catalog and rewrites `catalog.jsonl` and `snapshot.json` under the vault's `workflows/library/`. It parses HTML with the standard library, never executes or follows anything in the page, and keeps metadata only.

```sh
python3 scripts/catalog_workflows.py /path/outside/the/repo/catalog.html
```

It refuses to proceed when the card count does not match the page's own "workflows shown" figure, when the closing `</html>` is missing, or when any discussion URL is duplicated or not on the expected host. Entries whose URLs appear in the vault's `workflows/manifest.json` keep the adopted status; the rest are classified by the regular expressions in `RULES`, which is heuristic screening and not a semantic review. After a refresh, update the `retrieved_on` date in the script if it still says 2026-11-08, then run the tests.

## Tests

Offline, standard-library `unittest`, no network and no Claude call. The suite also holds the `duet` tests, so run it with the virtual environment. The workflow tests read the manifest, playbooks, and catalog from the configured vault, so `KARTEEK_VAULT` must be set.

```sh
.venv/bin/python -m unittest discover -s tests -v
```

| File | Covers |
| --- | --- |
| `tests/test_checkin.py` | Result validation accepts a good payload and rejects errors, missing evidence, duplicate keys, unsourced memories and unresolved references; duplicate suppression, dismissal surviving a new revision, snooze expiry; a whole check-in against a stand-in Claude lands in the graph and the daily note; a gate refusal or a failed run writes nothing; a sign-in failure is named without echoing provider text; the schedule file is weekdays only, has no `RunAtLoad`, and its install steps are the expected launchctl calls. |
| `tests/test_daily.py` | Draft headings are demoted; a note parses into keyed sections; status and draft edits reach the graph, a deleted section dismisses, a missing note changes nothing, an unreadable status is reported. |
| `tests/test_mcp_write.py` | The four write tools: private and assistant-owned, a claim always a hypothesis with its source, gate refusals named and journaled, a confirmed claim not overwritable, a commitment owner only when given. |
| `tests/test_workflows.py` | The vault's catalog, snapshot and manifest agree: every entry counted once, every playbook path exists, every manifest source is marked adopted in the catalog; a coding-titled entry is classed as engineering reference; the HTML parser treats embedded text as data. |
| `tests/test_config.py`, `test_graph.py`, `test_gates.py`, `test_parse.py`, `test_ecl.py`, `test_project.py`, `test_mcp_server.py`, `test_cli.py` | The `duet` package against the fixture vault in `tests/fixtures/vault/`: paths, store and lock, each gate, the gated write path, the parser, the indexer, the views, the MCP tools and the command line. Described in the top-level README. |

There is no end-to-end test of a live Claude run: `test_cli.py` runs `duet checkin` against a stand-in `claude` on `PATH`. A real run remains a manual step described in the vault's `integrations/README.md`.

# Karteek Clone — code

The code half of Karteek Clone. The other half is an Obsidian vault that holds everything that is knowledge: operating instructions, identity and memory pages, sources, templates, the 34 workflow playbooks, the external catalog, integration notes, and the ignored `private/` folder. This repo never contains personal content.

| Lives here | Lives in the vault |
| --- | --- |
| `duet/`, the governed knowledge graph: indexer, gates, views, read-only MCP server | `AGENTS.md`, `index.md`, `log.md`, `.mcp.json` |
| `scripts/check_in.py`, the bounded proactive runner | `private/graph/` and `private/views/`, written by `duet` |
| `scripts/catalog_workflows.py`, the catalog importer | `identity/`, `memory/`, `sources/`, `templates/`, `evaluations/` |
| `duet/config.py`, vault discovery (`scripts/config.py` is a shim) | `workflows/` with `manifest.json`, playbooks, `check-in-prompt.md`, `library/` |
| `tests/`, offline unit tests with a fixture vault | `wiki/`, `books/`, `articles/`, `posts/` |
| `.env.example`, this README | `integrations/`, `docs/` including the architecture page, `private/` |

## Setup

1. Point the code at the vault. Copy `.env.example` to `.env` and set `KARTEEK_VAULT` to the vault directory. The environment variable of the same name overrides the file. The scripts refuse to run when the path has no `AGENTS.md`.
2. Create the Python 3.12 environment and install the package: `/opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install -e .`. LadybugDB has no wheel for Python 3.14. The two scripts under `scripts/` still run on the standard library alone.
3. For a real check-in: Claude Code on `PATH`, signed in, with the vault's MCP providers authenticated through `/mcp` in an interactive session started inside the vault.

## Commands

```sh
python3 scripts/check_in.py --dry-run      # print the Claude command; touch nothing
python3 scripts/check_in.py                # one check-in; writes under <vault>/private/
python3 scripts/check_in.py --notify       # same, plus a fixed-text macOS notification
python3 scripts/check_in.py --write-schedule --hour 9 --minute 0   # generate a launchd plist under <vault>/private/; not installed
python3 scripts/catalog_workflows.py /path/outside/repo/catalog.html   # re-screen the workflow catalog into <vault>/workflows/library/
.venv/bin/python -m unittest discover -s tests -v   # 65 offline tests, about 15 seconds, no network
```

The runner reads the prompt, manifest, and MCP configuration from the vault and runs Claude with the vault as its working directory, so the read tools see vault paths. Everything it writes goes under the vault's `private/` folder. Details of each script, its flags, and the tests are in [scripts/README.md](scripts/README.md).

## Duet: the knowledge graph

`duet` turns the vault's pages into a graph that only accepts sourced, well-formed knowledge, then gives it back as Markdown views and MCP read tools. The design is the vault's `docs/specs/2026-11-08-karteek-duet-design.md`; this is phase 1 of three.

```sh
.venv/bin/python -m duet index            # index changed pages, then refresh the views; exit 1 if any page is rejected
.venv/bin/python -m duet index --force    # re-index every page
.venv/bin/python -m duet project          # re-render the views only
.venv/bin/python -m duet status           # vault, database, last run, node counts
.venv/bin/python -m duet rebuild          # delete the graph, re-index, replay MCP-written entries from the journal
.venv/bin/python -m duet serve            # read-only MCP server over stdio (the vault's .mcp.json starts it)
```

| Module | What it does |
| --- | --- |
| `duet/schema.py` | The model: 12 node tables, 14 relations, the allowed statuses and writers |
| `duet/changeset.py` | The only shape a writer can hand the graph: nodes to upsert, edges to merge, ids to retire, ids to unlink |
| `duet/gates.py` | The rules a changeset must pass whole: known writer, valid endpoints, a source for every claim, a statement source or a correction for `confirmed`, a known or explicitly unknown commitment owner, no tracked-to-private links |
| `duet/graph.py` | Owns the LadybugDB file, the writer lock, the one write path `apply`, and the journal |
| `duet/parse.py` | Reads a page into frontmatter, links, claim blocks and feedback callouts |
| `duet/ecl.py` | The indexer: definitions first (source pages before the claims that cite them), cross-page links second; skips unchanged pages by hash; retires what a page no longer defines |
| `duet/project.py` | Writes `me.md`, `review-queue.md`, `stale.md`, `commitments.md`, `decisions.md`, `index-report.md` |
| `duet/mcp_server.py` | Tools `get_entity`, `related`, `search`, `claims`, `query`, `duet_status`; opens the graph read-only per call, so answers follow the last index run |

What becomes what: a page under `sources/` with `type: personal-source` is a Source; a section with a `**Claim:**` field is a Claim, supported by the `sources/` pages its Evidence field links; a `> [!feedback]` callout is a Feedback that corrects the claim it links; a `type: knowledge` page is a Concept; every page is a Document with its links. Pages under `templates/` are Documents only. Everything `duet` writes is under the vault's `private/graph/` and `private/views/`, which the vault's Git ignores. The vault stays the source of truth: `rebuild` recreates the graph from the pages and the journal.

## How the two halves relate

The vault's `docs/architecture.md` is the system map and the place to read first. In short: the vault is the model, this code is the agent tooling around it. The code may read any vault file and may write only under `private/` and `workflows/library/`. Changes to knowledge are made in the vault, by Karteek or by an assistant following the vault's `AGENTS.md`.

Keep `.env` out of Git; it is already ignored. Keep secrets out of both repositories.

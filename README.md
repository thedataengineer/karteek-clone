# Karteek Clone — code

The code half of Karteek Clone. The other half is an Obsidian vault that holds everything that is knowledge: operating instructions, identity and memory pages, sources, templates, the 34 workflow playbooks, the external catalog, integration notes, and the ignored `private/` folder. This repo never contains personal content.

| Lives here | Lives in the vault |
| --- | --- |
| `duet/`, the governed knowledge graph: indexer, gates, views, MCP server, check-in runner | `AGENTS.md`, `index.md`, `log.md`, `.mcp.json` |
| `duet/checkin.py`, the bounded proactive runner and its schedule | `private/graph/`, `private/views/` and `private/duet/`, written by `duet` |
| `scripts/catalog_workflows.py`, the catalog importer | `identity/`, `memory/`, `sources/`, `templates/`, `evaluations/` |
| `duet/config.py`, vault discovery (`scripts/config.py` is a shim) | `workflows/` with `manifest.json`, playbooks, `check-in-prompt.md`, `library/` |
| `tests/`, offline unit tests with a fixture vault | `wiki/`, `books/`, `articles/`, `posts/` |
| `.env.example`, this README | `integrations/`, `docs/` including the architecture page, `private/` |

## Setup

1. Point the code at the vault. Copy `.env.example` to `.env` and set `KARTEEK_VAULT` to the vault directory. The environment variable of the same name overrides the file. The scripts refuse to run when the path has no `AGENTS.md`.
2. Create the Python 3.12 environment and install the package: `/opt/homebrew/bin/python3.12 -m venv .venv && .venv/bin/pip install -e .`. LadybugDB has no wheel for Python 3.14. The catalog importer under `scripts/` still runs on the standard library alone.
3. For a real check-in: Claude Code on `PATH` and signed in (`claude auth login`), with the vault's MCP providers authenticated through `/mcp` in an interactive session started inside the vault.

## Commands

```sh
.venv/bin/python -m duet checkin --dry-run   # print the Claude command; touch nothing
.venv/bin/python -m duet checkin --notify    # one check-in: graph, daily note, and a fixed-text macOS notification
.venv/bin/python -m duet schedule --hour 7 --minute 0 --notify --install   # weekday schedule, loaded with launchd
.venv/bin/python -m duet schedule --remove   # unload the schedule
python3 scripts/catalog_workflows.py /path/outside/repo/catalog.html   # re-screen the workflow catalog into <vault>/workflows/library/
.venv/bin/python -m unittest discover -s tests -v   # 95 offline tests, about 30 seconds, no network
```

The check-in reads the prompt, manifest, and MCP configuration from the vault and runs Claude with the vault as its working directory, so the read tools see vault paths. Claude only reads; the runner checks its result and writes it. Everything written goes under the vault's `private/` folder. The catalog importer and the tests are described in [scripts/README.md](scripts/README.md).

## Duet: the knowledge graph

`duet` turns the vault's pages into a graph that only accepts sourced, well-formed knowledge, then gives it back as Markdown views and MCP tools, and runs the check-in that prepares work on a schedule. The design is the vault's `docs/specs/2026-11-08-karteek-duet-design.md`; phases 1 and 2 of three are built.

```sh
.venv/bin/python -m duet index            # index changed pages, then refresh the views; exit 1 if any page is rejected
.venv/bin/python -m duet index --force    # re-index every page
.venv/bin/python -m duet project          # re-render the views only
.venv/bin/python -m duet status           # vault, database, last run, node counts
.venv/bin/python -m duet rebuild          # delete the graph, re-index, replay check-in and MCP entries from the journal, re-read the daily notes
.venv/bin/python -m duet serve            # MCP server over stdio: read tools and gated write tools (the vault's .mcp.json starts it)
.venv/bin/python -m duet serve --read-only   # the read tools alone
```

| Module | What it does |
| --- | --- |
| `duet/schema.py` | The model: 12 node tables, 14 relations, the allowed statuses and the three writers (`indexer`, `mcp:claude`, `runner`) |
| `duet/changeset.py` | The only shape a writer can hand the graph: nodes to upsert, edges to merge, ids to retire, ids to unlink |
| `duet/gates.py` | The rules a changeset must pass whole: known writer, valid endpoints, a source for every claim, a statement source or a correction for `confirmed`, a known or explicitly unknown commitment owner, no tracked-to-private links |
| `duet/graph.py` | Owns the LadybugDB file, the writer lock, the one write path `apply`, and the journal |
| `duet/parse.py` | Reads a page into frontmatter, links, claim blocks and feedback callouts |
| `duet/ecl.py` | The indexer: definitions first (source pages before the claims that cite them), cross-page links second; skips unchanged pages by hash; retires what a page no longer defines |
| `duet/project.py` | Writes `me.md`, `review-queue.md`, `stale.md`, `commitments.md`, `decisions.md`, `items.md`, `open-questions.md`, `index-report.md`, and a page per meeting and per account |
| `duet/mcp_server.py` | Read tools `get_entity`, `related`, `search`, `claims`, `query`, `duet_status`, opened read-only per call so answers follow the last run. Write tools `record_source`, `propose_claim`, `record_decision`, `record_commitment`: private, assistant-owned, gated, and a claim is always a hypothesis |
| `duet/checkin.py` | One check-in: index, run Claude with read tools only, validate the result, suppress what was already surfaced, apply one gated changeset as `runner`, append the daily note, notify. Also writes, installs and removes the launchd schedule |
| `duet/daily.py` | The daily note `private/duet/YYYY-MM-DD.md`: renders item sections, and reads `status:` lines and drafts back into the graph on every index |

What becomes what: a page under `sources/` with `type: personal-source` is a Source; a section with a `**Claim:**` field is a Claim, supported by the `sources/` pages its Evidence field links; a `> [!feedback]` callout is a Feedback that corrects the claim it links; a `type: knowledge` page is a Concept; every page is a Document with its links. Pages under `templates/` are Documents only. A check-in adds what no page holds: meetings, decisions, commitments, account context and open questions as private records that each cite the provider record, and Duet items that Karteek marks `done`, `dismissed` or `snoozed YYYY-MM-DD` in the daily note. Everything `duet` writes is under the vault's `private/graph/`, `private/views/` and `private/duet/`, which the vault's Git ignores. The vault stays the source of truth: `rebuild` recreates the graph from the pages, the journal and the daily notes.

## How the two halves relate

The vault's `docs/architecture.md` is the system map and the place to read first. In short: the vault is the model, this code is the agent tooling around it. The code may read any vault file and may write only under `private/` and `workflows/library/`. Changes to knowledge are made in the vault, by Karteek or by an assistant following the vault's `AGENTS.md`.

Keep `.env` out of Git; it is already ignored. Keep secrets out of both repositories.

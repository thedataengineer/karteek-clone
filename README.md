# Karteek Clone — code

The code half of Karteek Clone. The other half is an Obsidian vault that holds everything that is knowledge: operating instructions, identity and memory pages, sources, templates, the 30 workflow playbooks, the external catalog, integration notes, and the ignored `private/` folder. This repo never contains personal content.

| Lives here | Lives in the vault |
| --- | --- |
| `scripts/check_in.py`, the bounded proactive runner | `AGENTS.md`, `index.md`, `log.md`, `.mcp.json` |
| `scripts/catalog_workflows.py`, the catalog importer | `identity/`, `memory/`, `sources/`, `templates/`, `evaluations/` |
| `scripts/config.py`, vault discovery | `workflows/` with `manifest.json`, playbooks, `check-in-prompt.md`, `library/` |
| `tests/`, offline unit tests | `wiki/`, `books/`, `articles/`, `posts/` |
| `.env.example`, this README | `integrations/`, `docs/` including the architecture page, `private/` |

## Setup

1. Point the code at the vault. Copy `.env.example` to `.env` and set `KARTEEK_VAULT` to the vault directory. The environment variable of the same name overrides the file. The scripts refuse to run when the path has no `AGENTS.md`.
2. Python 3.9 or newer, standard library only. Nothing to install.
3. For a real check-in: Claude Code on `PATH`, signed in, with the vault's MCP providers authenticated through `/mcp` in an interactive session started inside the vault.

## Commands

```sh
python3 scripts/check_in.py --dry-run      # print the Claude command; touch nothing
python3 scripts/check_in.py                # one check-in; writes under <vault>/private/
python3 scripts/check_in.py --notify       # same, plus a fixed-text macOS notification
python3 scripts/check_in.py --write-schedule --hour 9 --minute 0   # generate a launchd plist under <vault>/private/; not installed
python3 scripts/catalog_workflows.py /path/outside/repo/catalog.html   # re-screen the workflow catalog into <vault>/workflows/library/
python3 -m unittest discover -s tests -v   # offline tests, a few seconds, no network
```

The runner reads the prompt, manifest, and MCP configuration from the vault and runs Claude with the vault as its working directory, so the read tools see vault paths. Everything it writes goes under the vault's `private/` folder. Details of each script, its flags, and the tests are in [scripts/README.md](scripts/README.md).

## How the two halves relate

The vault's `docs/architecture.md` is the system map and the place to read first. In short: the vault is the model, this code is the agent tooling around it. The code may read any vault file and may write only under `private/` and `workflows/library/`. Changes to knowledge are made in the vault, by Karteek or by an assistant following the vault's `AGENTS.md`.

Keep `.env` out of Git; it is already ignored. Keep secrets out of both repositories.

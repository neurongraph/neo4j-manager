# neo4j-manager

Manage multiple named local Neo4j databases (`colima -> docker -> neo4j`), each
with its own ports and persistent data folders tracked in a config file, and
sync a database's content between machines via a linked GitHub repo.

## Prerequisites

```bash
brew install colima docker
```

## Install

```bash
uv tool install --editable .
```

This installs the `neo4j-manager` command. For local development:

```bash
uv sync
uv run neo4j-manager --help
```

A [`justfile`](justfile) wraps the common actions -- run `just` (or
`just --list`) to see them:

```bash
just install     # uv tool install --editable . (system-wide neo4j-manager)
just reinstall    # force-reinstall (e.g. after adding a dependency)
just uninstall     # uv tool uninstall neo4j-manager
just sync          # uv sync (dev venv)
just run status     # uv run neo4j-manager <args>, without installing
just tui             # uv run neo4j-manager tui
just check            # sanity-check that every module still imports
just clean             # remove .venv/dist/__pycache__
```

## Interactive TUI

```bash
neo4j-manager tui
```

Launches a full-screen dashboard instead of remembering flags: a live table of
instances (`n` new, `enter` open, `s`/`x`/`r` start/stop/restart, `l` shell,
`b` open in browser, `p`/`u` sync push/pull, `D` remove), and a per-instance detail screen where you
can view and edit its settings as a form -- sync settings (data repo URL,
export branch) save immediately, instance settings (image, plugins, ports)
recreate the container on save (your data on disk is untouched). The plain
flag-based commands below still work exactly as before for scripting.

## Usage

```bash
# Create and start a new instance (auto-picks free ports, generates a password)
neo4j-manager create mydb

# Lifecycle
neo4j-manager status
neo4j-manager stop mydb
neo4j-manager start mydb
neo4j-manager restart mydb
neo4j-manager remove mydb --purge-data

# Interactive Cypher shell
neo4j-manager shell mydb

# Link to a GitHub repo and sync data across machines
neo4j-manager sync init mydb --repo git@github.com:you/mydb-neo4j-data.git --create
neo4j-manager sync push mydb -m "end of session"

# On another machine
neo4j-manager create mydb --no-start
neo4j-manager sync pull mydb --repo git@github.com:you/mydb-neo4j-data.git
```

`sync push`/`sync pull` are full-snapshot operations (export/replace the whole
graph via APOC's Cypher export), not incremental merges -- they match a
single-session, single-writer workflow: finish working on one machine, push,
then pull on the next machine before starting a new session there.

Config lives at `~/.config/neo4j-manager/config.toml` (not synced; holds
per-machine ports/paths/credentials and each instance's linked repo URL).

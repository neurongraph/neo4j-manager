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
just dump mydb          # one-off binary snapshot (see below)
just load mydb            # restore a one-off binary snapshot (see below)
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

The instance list also shows memory: a header line with the Colima VM's CPUs,
RAM used/available, total container usage, and an estimated peak for the
running instances (flagged when it exceeds the VM's RAM), plus per-instance
`Mem used`, `Mem limit`, `Heap` and `Page cache` columns.

## Memory settings

All instances share one Colima VM. With nothing configured, each Neo4j
container sees the whole VM and sizes itself from it: a max heap of 1/4 of VM
RAM plus the image's 512M page cache, plus JVM overhead. Several busy
instances can exhaust the VM, and the kernel then SIGKILLs one (exit 137). Each
instance can optionally set:

| Setting | CLI flag (`create`) | Effect |
|---|---|---|
| `heap_size` | `--heap 1g` | `server.memory.heap.initial_size` and `max_size` |
| `pagecache_size` | `--pagecache 512m` | `server.memory.pagecache.size` |
| `memory_limit` | `--memory-limit 2g` | `docker run --memory`: a hard cap for the container |

Sizes are a whole number plus `k`/`m`/`g`; blank (the default) keeps Neo4j's
and Docker's own behaviour. They're applied when the container is created, so
changing them on the TUI's detail screen recreates that container (data on
disk is untouched). Keep `memory_limit` comfortably above heap + page cache
(e.g. heap `1g`, page cache `512m`, limit `2g`).

## Usage

```bash
# Create and start a new instance (auto-picks free ports, generates a password)
neo4j-manager create mydb

# ...optionally with explicit memory sizing (see "Memory settings")
neo4j-manager create mydb --heap 1g --pagecache 512m --memory-limit 2g

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

## Scripting / JSON output

Pass `--json` to `create`, `status`, or `list` to get machine-readable output instead of the rich table.
The JSON is printed to **stdout** only; errors go to **stderr** with a non-zero exit code, so
`json.loads(stdout)` either succeeds or the caller sees the failure on stderr.

```bash
# Create an instance and wait until Neo4j is ready to accept connections
neo4j-manager create demo --json --wait | python3 -c \
  'import json,sys; d=json.load(sys.stdin); print(d["bolt_url"], d["password"])'

# Look up an existing instance (idempotent: create if missing, status if present)
neo4j-manager status demo --json

# List all instances as a JSON array (each entry includes password and bolt_url)
neo4j-manager list --json
```

Every object has the same shape:

```json
{
  "name": "demo",
  "container_name": "neo4j-demo",
  "state": "running",
  "bolt_url": "bolt://127.0.0.1:7688",
  "http_url": "http://127.0.0.1:7475",
  "bolt_port": 7688,
  "http_port": 7475,
  "user": "neo4j",
  "password": "...",
  "image": "neo4j:latest",
  "data_dir": "...",
  "heap_size": "",
  "pagecache_size": "",
  "memory_limit": ""
}
```

`--wait` blocks `create` until `cypher-shell "RETURN 1"` succeeds inside the container (default
timeout: 60 s, override with `--timeout SECONDS`). Use it to avoid racing the JVM startup.
`--wait` with `--no-start` is a usage error.

## One-off binary dump/load

```bash
just dump mydb   # writes ~/neo4j-data/mydb/import/neo4j.dump
just load mydb   # restores it (OVERWRITES mydb's current data)
```

For a manual, one-off transfer of a full database -- not part of `sync push`/
`sync pull`, and not meant to be committed to git. Uses `neo4j-admin database
dump`/`load`: a faithful binary snapshot (indexes/constraints included), much
faster than the Cypher export for large graphs, but an opaque blob rather than
a diffable text file, and the two machines' Neo4j image versions should match.
Both recipes briefly stop the instance's container (via a throwaway `docker
run` against the same volumes, matching Neo4j's official offline-dump
pattern) and restart it afterward if it was running. To move the dump to
another machine, copy `~/neo4j-data/mydb/import/neo4j.dump` over yourself
(scp, cloud drive, etc.) into that machine's `mydb` import dir before running
`just load mydb` there.

Config lives at `~/.config/neo4j-manager/config.toml` (not synced; holds
per-machine ports/paths/credentials and each instance's linked repo URL).

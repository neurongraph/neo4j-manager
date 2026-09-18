# neo4j-manager project tasks. Run `just` with no args to list them.

default:
    @just --list

# Install/update Python deps into the local dev venv (.venv)
sync:
    uv sync

# Make `neo4j-manager` available system-wide (editable: source changes apply immediately)
install:
    uv tool install --editable .

# Force-reinstall the system-wide tool (e.g. after adding/removing a dependency)
reinstall:
    uv tool install --editable . --reinstall

# Remove the system-wide `neo4j-manager` command
uninstall:
    uv tool uninstall neo4j-manager

# Run the CLI from source without installing, e.g. `just run status`
run *args:
    uv run neo4j-manager {{args}}

# Launch the interactive TUI dashboard from source
tui:
    uv run neo4j-manager tui

# Quick sanity check that every module still imports cleanly
check:
    uv run python -c "from neo4j_manager import config, ports, docker_ops, instance, sync, cli, tui; print('OK')"

# Remove local build/venv artifacts
clean:
    rm -rf .venv dist *.egg-info
    find . -name __pycache__ -type d -exec rm -rf {} +

# --- one-off binary dump/load (neo4j-admin) ---
# A full binary snapshot of the whole store (indexes/constraints included),
# much faster than `sync push/pull` for large graphs -- but NOT git-diffable
# and not part of that mechanism. Use only for a manual, one-off transfer
# between machines running matching Neo4j image versions. Briefly stops the
# instance (restarted automatically afterward if it was running).

# Stop <name>, dump it to <import_dir>/neo4j.dump (overwrites a previous dump), restart it if it was running
dump name:
    #!/usr/bin/env bash
    set -euo pipefail
    IFS=$'\t' read -r container image data_dir import_dir < <(uv run python -c "
    import tomllib, pathlib
    c = tomllib.load(open(pathlib.Path.home() / '.config/neo4j-manager/config.toml', 'rb'))
    i = c['instances']['{{name}}']
    print(i['container_name'], i['image'], i['data_dir'], i['import_dir'], sep='\t')
    ")
    running=$(docker ps --filter "name=^${container}\$" --filter status=running -q)
    docker stop "$container" >/dev/null 2>&1 || true
    set +e
    docker run --rm -v "$data_dir":/data -v "$import_dir":/var/lib/neo4j/import "$image" \
        neo4j-admin database dump neo4j --to-path=/var/lib/neo4j/import --overwrite-destination=true
    status=$?
    set -e
    [ -n "$running" ] && docker start "$container" >/dev/null
    if [ "$status" -ne 0 ]; then exit "$status"; fi
    echo "Dumped to $import_dir/neo4j.dump"

# Stop <name>, load <import_dir>/neo4j.dump into it (OVERWRITES current data!), restart if it was running
load name:
    #!/usr/bin/env bash
    set -euo pipefail
    IFS=$'\t' read -r container image data_dir import_dir < <(uv run python -c "
    import tomllib, pathlib
    c = tomllib.load(open(pathlib.Path.home() / '.config/neo4j-manager/config.toml', 'rb'))
    i = c['instances']['{{name}}']
    print(i['container_name'], i['image'], i['data_dir'], i['import_dir'], sep='\t')
    ")
    running=$(docker ps --filter "name=^${container}\$" --filter status=running -q)
    docker stop "$container" >/dev/null 2>&1 || true
    set +e
    docker run --rm -v "$data_dir":/data -v "$import_dir":/var/lib/neo4j/import "$image" \
        neo4j-admin database load neo4j --from-path=/var/lib/neo4j/import --overwrite-destination=true
    status=$?
    set -e
    [ -n "$running" ] && docker start "$container" >/dev/null
    if [ "$status" -ne 0 ]; then exit "$status"; fi
    echo "Loaded $import_dir/neo4j.dump into $container"

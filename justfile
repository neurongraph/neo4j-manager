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

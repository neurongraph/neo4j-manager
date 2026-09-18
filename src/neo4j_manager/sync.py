"""Cross-machine data sync: full-graph Cypher export/import plus git push/pull.

Snapshot semantics, not merge: `push` exports the whole graph and commits it;
`pull` replaces the local instance's data entirely with the repo's snapshot.
Matches a single-writer-per-session workflow -- no concurrent-edit resolution.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from neo4j_manager import config as cfg
from neo4j_manager import docker_ops
from neo4j_manager import instance as inst

EXPORT_FILENAME = "export.cypher"
EXPORT_CYPHER = (
    "CALL apoc.export.cypher.all("
    f"'{EXPORT_FILENAME}', "
    "{format: 'cypher-shell', useOptimizations: {type: 'UNWIND_BATCH', unwindBatchSize: 20}}"
    ");"
)


class SyncError(RuntimeError):
    pass


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)


def init_repo(name: str, repo_url: str, create: bool = False) -> None:
    config, instance = inst.get(name)
    if create:
        result = subprocess.run(["gh", "repo", "create", repo_url, "--private"], text=True, capture_output=True)
        if result.returncode != 0:
            raise SyncError(f"gh repo create failed:\n{result.stdout}\n{result.stderr}")

    base = instance.expanded("data_dir").parent
    repo_path = base / "repo"
    repo_path.parent.mkdir(parents=True, exist_ok=True)

    if not repo_path.exists():
        result = _git(["clone", repo_url, str(repo_path)], cwd=repo_path.parent)
        if result.returncode != 0:
            # Empty remote repos fail to clone; fall back to init + remote add.
            repo_path.mkdir(parents=True, exist_ok=True)
            _git(["init", "-b", instance.export_branch], cwd=repo_path)
            _git(["remote", "add", "origin", repo_url], cwd=repo_path)

    # Normalize the local branch name regardless of the machine's git default
    # (init.defaultBranch) or an existing clone's checked-out branch, so it
    # always matches the configured export_branch.
    _git(["checkout", "-B", instance.export_branch], cwd=repo_path)

    instance.data_repo = repo_url
    instance.data_repo_path = str(repo_path)
    config.instances[name] = instance
    cfg.save(config)


def _ensure_repo(instance: cfg.Instance) -> Path:
    if not instance.data_repo:
        raise SyncError(f"Instance {instance.name!r} has no data_repo configured. Run `sync init` first.")
    repo_path = instance.expanded("data_repo_path")
    if not repo_path.exists():
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        result = _git(["clone", instance.data_repo, str(repo_path)], cwd=repo_path.parent)
        if result.returncode != 0:
            raise SyncError(f"git clone failed:\n{result.stdout}\n{result.stderr}")
        _git(["checkout", "-B", instance.export_branch], cwd=repo_path)
    return repo_path


def push(name: str, message: str = "") -> None:
    _, instance = inst.get(name)
    state = docker_ops.container_status(instance.container_name)
    if state != "running":
        raise SyncError(f"Instance {name!r} is not running -- start it first (`neo4j-manager start {name}`).")

    result = docker_ops.cypher_shell(instance, EXPORT_CYPHER, timeout=120)
    if result.returncode != 0:
        raise SyncError(f"apoc export failed:\n{result.stdout}\n{result.stderr}")

    exported = instance.expanded("import_dir") / EXPORT_FILENAME
    if not exported.exists():
        raise SyncError(f"Export did not produce {exported}")

    repo_path = _ensure_repo(instance)
    shutil.copy(exported, repo_path / EXPORT_FILENAME)

    _git(["add", EXPORT_FILENAME], cwd=repo_path)
    commit_msg = message or f"Sync {name}"
    commit = _git(["commit", "-m", commit_msg], cwd=repo_path)
    if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
        raise SyncError(f"git commit failed:\n{commit.stdout}\n{commit.stderr}")

    push_result = _git(["push", "-u", "origin", instance.export_branch], cwd=repo_path)
    if push_result.returncode != 0:
        raise SyncError(f"git push failed:\n{push_result.stdout}\n{push_result.stderr}")


def status(name: str) -> dict:
    _, instance = inst.get(name)
    repo_path = instance.expanded("data_repo_path")
    if not instance.data_repo or not repo_path.exists():
        return {"configured": False}

    _git(["fetch"], cwd=repo_path)
    dirty = _git(["status", "--short"], cwd=repo_path).stdout.strip()
    counts = _git(
        ["rev-list", "--left-right", "--count", f"HEAD...origin/{instance.export_branch}"], cwd=repo_path
    ).stdout.strip()
    ahead, behind = (counts.split() + ["0", "0"])[:2] if counts else ("0", "0")
    return {
        "configured": True,
        "dirty": bool(dirty),
        "dirty_files": dirty,
        "ahead": int(ahead),
        "behind": int(behind),
    }


def pull(name: str, repo_url: str | None = None, force: bool = False) -> None:
    config, instance = inst.get(name)

    if repo_url and repo_url != instance.data_repo:
        instance.data_repo = repo_url
        repo_path = instance.expanded("data_dir").parent / "repo"
        instance.data_repo_path = str(repo_path)
        config.instances[name] = instance
        cfg.save(config)

    repo_path = _ensure_repo(instance)

    st = status(name)
    if st.get("dirty") and not force:
        raise SyncError(
            f"Local data repo for {name!r} has unpushed changes:\n{st['dirty_files']}\n"
            "Push them first, or re-run with --force to discard."
        )

    pull_result = _git(["pull", "origin", instance.export_branch], cwd=repo_path)
    if pull_result.returncode != 0:
        raise SyncError(f"git pull failed:\n{pull_result.stdout}\n{pull_result.stderr}")

    export_file = repo_path / EXPORT_FILENAME
    if not export_file.exists():
        raise SyncError(f"No {EXPORT_FILENAME} found in {repo_path} -- nothing to import yet.")

    state = docker_ops.container_status(instance.container_name)
    if state is not None:
        docker_ops.docker_stop(instance.container_name)
        docker_ops.docker_rm(instance.container_name)

    data_dir = instance.expanded("data_dir")
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    docker_ops.docker_run(instance)
    docker_ops.wait_ready(instance)

    cypher_text = export_file.read_text()
    result = docker_ops.cypher_shell(instance, cypher_text, timeout=600)
    if result.returncode != 0:
        raise SyncError(f"Import failed:\n{result.stdout}\n{result.stderr}")

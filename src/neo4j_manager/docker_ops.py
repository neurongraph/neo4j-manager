"""Thin subprocess wrappers around the `colima` and `docker` CLIs.

No Docker SDK dependency -- shells out, same as the reference workflow this tool
is based on. One shared Colima VM; each instance is an independently named/ported
`docker run` container.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass

from neo4j_manager.config import Instance


class DockerOpsError(RuntimeError):
    pass


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, **kwargs)


def require_tools() -> None:
    missing = [t for t in ("colima", "docker") if shutil.which(t) is None]
    if missing:
        raise SystemExit(
            f"Missing required tool(s): {', '.join(missing)}. Install with `brew install colima docker`."
        )


def colima_running(profile: str = "default") -> bool:
    result = _run(["colima", "status", "--profile", profile], capture_output=True)
    return result.returncode == 0 and "Running" in (result.stdout + result.stderr)


def colima_start(profile: str = "default") -> None:
    if colima_running(profile):
        return
    result = _run(["colima", "start", "--profile", profile], capture_output=True)
    if result.returncode != 0:
        raise DockerOpsError(f"colima start failed:\n{result.stdout}\n{result.stderr}")


def container_status(container_name: str) -> str | None:
    """Returns 'running', 'exited', etc, or None if the container doesn't exist."""
    result = _run(
        ["docker", "ps", "-a", "--filter", f"name=^{container_name}$", "--format", "{{json .}}"],
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    data = json.loads(result.stdout.strip().splitlines()[0])
    return data.get("State")


def docker_run(instance: Instance) -> None:
    for attr in ("data_dir", "logs_dir", "import_dir", "plugins_dir"):
        instance.expanded(attr).mkdir(parents=True, exist_ok=True)

    env = [
        "-e", f"NEO4J_AUTH={instance.auth_user}/{instance.auth_password}",
    ]
    if "apoc" in instance.plugins:
        env += [
            "-e", "NEO4J_PLUGINS=[\"apoc\"]",
            "-e", "NEO4J_dbms_security_procedures_unrestricted=apoc.*",
            "-e", "NEO4J_apoc_export_file_enabled=true",
        ]

    cmd = [
        "docker", "run",
        "--name", instance.container_name,
        "-p", f"{instance.http_port}:7474",
        "-p", f"{instance.bolt_port}:7687",
        "-d",
        "-v", f"{instance.expanded('data_dir')}:/data",
        "-v", f"{instance.expanded('logs_dir')}:/logs",
        "-v", f"{instance.expanded('import_dir')}:/var/lib/neo4j/import",
        "-v", f"{instance.expanded('plugins_dir')}:/plugins",
        *env,
        instance.image,
    ]
    result = _run(cmd, capture_output=True)
    if result.returncode != 0:
        raise DockerOpsError(f"docker run failed:\n{result.stdout}\n{result.stderr}")


def docker_start(container_name: str) -> None:
    result = _run(["docker", "start", container_name], capture_output=True)
    if result.returncode != 0:
        raise DockerOpsError(f"docker start failed:\n{result.stdout}\n{result.stderr}")


def docker_stop(container_name: str) -> None:
    result = _run(["docker", "stop", container_name], capture_output=True)
    if result.returncode != 0:
        raise DockerOpsError(f"docker stop failed:\n{result.stdout}\n{result.stderr}")


def docker_rm(container_name: str) -> None:
    _run(["docker", "rm", "-f", container_name], capture_output=True)


def cypher_shell(instance: Instance, cypher: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return _run(
        [
            "docker", "exec", "-i", instance.container_name,
            "cypher-shell", "-u", instance.auth_user, "-p", instance.auth_password,
        ],
        input=cypher,
        capture_output=True,
        timeout=timeout,
    )


def cypher_shell_interactive(instance: Instance) -> None:
    subprocess.run(
        [
            "docker", "exec", "-it", instance.container_name,
            "cypher-shell", "-u", instance.auth_user, "-p", instance.auth_password,
        ]
    )


def wait_ready(instance: Instance, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    last_err = ""
    while time.monotonic() < deadline:
        try:
            result = cypher_shell(instance, "RETURN 1;", timeout=10)
        except subprocess.TimeoutExpired:
            result = None
        if result is not None and result.returncode == 0:
            return
        last_err = result.stderr if result else "timed out"
        time.sleep(2)
    raise DockerOpsError(f"Neo4j did not become ready within {timeout}s: {last_err}")

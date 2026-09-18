"""Instance lifecycle: create/start/stop/restart/remove/status, backed by config + docker_ops."""

from __future__ import annotations

import secrets
import shutil
from pathlib import Path

from neo4j_manager import config as cfg
from neo4j_manager import docker_ops
from neo4j_manager.ports import allocate_ports

DEFAULT_BASE_DIR = Path.home() / "neo4j-manager"


def create(
    name: str,
    http_port: int | None = None,
    bolt_port: int | None = None,
    data_dir: str | None = None,
    plugins: list[str] | None = None,
    image: str = "neo4j:latest",
    repo: str = "",
    start: bool = True,
) -> cfg.Instance:
    config = cfg.load()
    if name in config.instances:
        raise SystemExit(f"Instance {name!r} already exists. Use a different name or `remove` it first.")

    if http_port is None or bolt_port is None:
        auto_http, auto_bolt = allocate_ports(config)
        http_port = http_port or auto_http
        bolt_port = bolt_port or auto_bolt

    base = Path(data_dir).expanduser() if data_dir else DEFAULT_BASE_DIR / name

    instance = cfg.Instance(
        name=name,
        container_name=f"neo4j-{name}",
        image=image,
        http_port=http_port,
        bolt_port=bolt_port,
        data_dir=str(base / "data"),
        logs_dir=str(base / "logs"),
        import_dir=str(base / "import"),
        plugins_dir=str(base / "plugins"),
        plugins=plugins or ["apoc"],
        auth_password=secrets.token_urlsafe(18),
        data_repo=repo,
        data_repo_path=str(base / "repo") if repo else "",
    )

    docker_ops.require_tools()
    docker_ops.colima_start(config.colima_profile)

    config.instances[name] = instance
    cfg.save(config)

    if start:
        docker_ops.docker_run(instance)

    return instance


def get(name: str) -> tuple[cfg.Config, cfg.Instance]:
    config = cfg.load()
    return config, cfg.get_instance(config, name)


def start(name: str) -> None:
    config, instance = get(name)
    docker_ops.require_tools()
    docker_ops.colima_start(config.colima_profile)
    state = docker_ops.container_status(instance.container_name)
    if state is None:
        docker_ops.docker_run(instance)
    else:
        docker_ops.docker_start(instance.container_name)


def stop(name: str) -> None:
    _, instance = get(name)
    docker_ops.docker_stop(instance.container_name)


def restart(name: str) -> None:
    stop(name)
    start(name)


def remove(name: str, purge_data: bool = False) -> None:
    config, instance = get(name)
    docker_ops.docker_rm(instance.container_name)
    if purge_data:
        for attr in ("data_dir", "logs_dir", "import_dir", "plugins_dir"):
            path = instance.expanded(attr)
            if path.exists():
                shutil.rmtree(path)
    del config.instances[name]
    cfg.save(config)


def shell(name: str) -> None:
    _, instance = get(name)
    docker_ops.cypher_shell_interactive(instance)


RECREATE_FIELDS = ("image", "plugins", "http_port", "bolt_port")


def update(
    name: str,
    *,
    image: str | None = None,
    plugins: list[str] | None = None,
    http_port: int | None = None,
    bolt_port: int | None = None,
) -> cfg.Instance:
    """Apply the given fields to a stored instance.

    Fields in RECREATE_FIELDS are baked into the container at `docker run`
    time, so changing any of them recreates the container (stop+rm+run) to
    take effect. The mounted volumes -- and therefore the actual graph data
    -- are untouched by this; only the container itself is replaced.
    """
    config, instance = get(name)

    updates = {
        "image": image,
        "plugins": plugins,
        "http_port": http_port,
        "bolt_port": bolt_port,
    }
    updates = {k: v for k, v in updates.items() if v is not None}
    changed = {k for k, v in updates.items() if getattr(instance, k) != v}
    for k, v in updates.items():
        setattr(instance, k, v)

    config.instances[name] = instance
    cfg.save(config)

    if changed & set(RECREATE_FIELDS):
        state = docker_ops.container_status(instance.container_name)
        if state is not None:
            docker_ops.docker_stop(instance.container_name)
            docker_ops.docker_rm(instance.container_name)
        docker_ops.docker_run(instance)

    return instance


def list_status() -> list[dict]:
    config = cfg.load()
    rows = []
    for name, instance in config.instances.items():
        state = docker_ops.container_status(instance.container_name) or "not created"
        rows.append(
            {
                "name": name,
                "state": state,
                "http_port": instance.http_port,
                "bolt_port": instance.bolt_port,
                "image": instance.image,
                "data_dir": instance.data_dir,
            }
        )
    return rows

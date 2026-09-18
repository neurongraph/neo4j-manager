"""Local config file: instance registry, ports, paths, credentials, data-repo links.

Lives at ~/.config/neo4j-manager/config.toml, never synced. Only the `data_repo`
URL field is meant to match the same value across machines (re-entered by the
user when setting an instance up on a new machine) -- everything else here is
host-specific.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

import tomli_w

CONFIG_DIR = Path(os.environ.get("NEO4J_MANAGER_HOME", Path.home() / ".config" / "neo4j-manager"))
CONFIG_PATH = CONFIG_DIR / "config.toml"

DEFAULT_HTTP_PORT = 7474
DEFAULT_BOLT_PORT = 7687


@dataclass
class Instance:
    name: str
    container_name: str
    image: str = "neo4j:latest"
    http_port: int = DEFAULT_HTTP_PORT
    bolt_port: int = DEFAULT_BOLT_PORT
    data_dir: str = ""
    logs_dir: str = ""
    import_dir: str = ""
    plugins_dir: str = ""
    plugins: list[str] = field(default_factory=lambda: ["apoc"])
    auth_user: str = "neo4j"
    auth_password: str = ""
    data_repo: str = ""
    data_repo_path: str = ""
    export_branch: str = "main"

    def expanded(self, attr: str) -> Path:
        return Path(getattr(self, attr)).expanduser()


@dataclass
class Config:
    colima_profile: str = "default"
    instances: dict[str, Instance] = field(default_factory=dict)


def _ensure_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)


def load() -> Config:
    if not CONFIG_PATH.exists():
        return Config()
    with open(CONFIG_PATH, "rb") as f:
        raw = tomllib.load(f)
    colima_profile = raw.get("colima", {}).get("profile", "default")
    instances = {}
    for name, data in raw.get("instances", {}).items():
        instances[name] = Instance(name=name, **{k: v for k, v in data.items() if k != "name"})
    return Config(colima_profile=colima_profile, instances=instances)


def save(config: Config) -> None:
    _ensure_dir()
    raw: dict = {"colima": {"profile": config.colima_profile}, "instances": {}}
    for name, inst in config.instances.items():
        d = asdict(inst)
        d.pop("name", None)
        raw["instances"][name] = d
    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump(raw, f)
    os.chmod(CONFIG_PATH, 0o600)


def get_instance(config: Config, name: str) -> Instance:
    try:
        return config.instances[name]
    except KeyError:
        raise SystemExit(f"No such instance: {name!r} (run `neo4j-manager list` to see known instances)")

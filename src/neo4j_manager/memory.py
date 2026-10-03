"""Memory sizing: parse/format byte sizes and summarize VM + per-instance memory.

Per-instance settings (heap_size, pagecache_size, memory_limit) are all
optional -- blank means "leave it to Neo4j/Docker", which for the official
image means a JVM-heuristic max heap of 1/4 of the RAM the container sees
(the whole Colima VM, absent a container limit) plus a 512M page cache.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from neo4j_manager import docker_ops
from neo4j_manager.config import Config, Instance

_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}

# Strict form accepted in config: what both Neo4j and `docker run --memory` understand.
_CONFIG_SIZE_RE = re.compile(r"^(\d+)([kmg])$", re.IGNORECASE)
# Looser form for parsing docker CLI output, e.g. "687.6MiB", "7.738GiB", "0B".
_DISPLAY_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)i?b?\s*$", re.IGNORECASE)

# Image default for server.memory.pagecache.size when unset.
DEFAULT_PAGECACHE = 512 * 1024**2
# Rough allowance for JVM memory outside heap + page cache (metaspace, threads, direct buffers).
JVM_OVERHEAD = 512 * 1024**2


def normalize_size(value: str) -> str:
    """Validate a config size like '512m' / '2G'; returns it lowercased, or '' for blank."""
    value = value.strip()
    if not value:
        return ""
    if not _CONFIG_SIZE_RE.match(value):
        raise ValueError(f"Invalid memory size {value!r}: use a whole number plus k/m/g, e.g. 512m or 2g")
    return value.lower()


def parse_size(value: str) -> int | None:
    """Bytes for '2g' or docker-style '687.6MiB'; None if blank/unparseable."""
    match = _DISPLAY_SIZE_RE.match(value or "")
    if not match:
        return None
    return int(float(match.group(1)) * _UNITS[match.group(2).lower()])


def format_bytes(n: int | None) -> str:
    if n is None:
        return "-"
    if n < 1024:
        return f"{n} B"
    value = float(n)
    for unit in ("KiB", "MiB", "GiB"):
        value /= 1024
        if value < 1024 or unit == "GiB":
            break
    return f"{value:.1f} {unit}"


def estimated_peak(instance: Instance, vm_total: int | None) -> int | None:
    """Rough upper bound on what an instance can grow to inside the VM."""
    if instance.memory_limit:
        return parse_size(instance.memory_limit)
    heap = parse_size(instance.heap_size) if instance.heap_size else (vm_total // 4 if vm_total else None)
    if heap is None:
        return None
    pagecache = parse_size(instance.pagecache_size) if instance.pagecache_size else DEFAULT_PAGECACHE
    return heap + pagecache + JVM_OVERHEAD


@dataclass
class Snapshot:
    profile: str
    cpus: int | None = None
    vm_total: int | None = None
    vm_available: int | None = None
    # container_name -> (used bytes, limit bytes); running containers only
    containers: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def containers_used(self) -> int:
        return sum(used for used, _ in self.containers.values())


def snapshot(config: Config) -> Snapshot:
    """Gather VM + container memory. Slow-ish (~1s for `docker stats`); call off the UI thread."""
    snap = Snapshot(profile=config.colima_profile)
    vm = docker_ops.colima_vm_info(config.colima_profile)
    if vm:
        snap.cpus = vm.get("cpus")
        snap.vm_total = vm.get("memory")
        if vm.get("status") == "Running":
            meminfo = docker_ops.vm_meminfo(config.colima_profile)
            snap.vm_available = meminfo.get("MemAvailable")
            snap.vm_total = meminfo.get("MemTotal", snap.vm_total)
    for name, usage in docker_ops.container_mem_usage().items():
        used, _, limit = usage.partition("/")
        used_bytes, limit_bytes = parse_size(used), parse_size(limit)
        if used_bytes is not None and limit_bytes is not None:
            snap.containers[name] = (used_bytes, limit_bytes)
    return snap


def vm_summary(config: Config, snap: Snapshot) -> tuple[str, bool]:
    """One-line VM summary for the TUI header, and whether running instances may overcommit the VM."""
    parts = [f"Colima {snap.profile!r}"]
    if snap.cpus:
        parts.append(f"{snap.cpus} CPU")
    if snap.vm_total is None:
        return " · ".join(parts + ["VM not found"]), False
    if snap.vm_available is not None:
        parts.append(
            f"VM {format_bytes(snap.vm_total - snap.vm_available)} / {format_bytes(snap.vm_total)} used "
            f"({format_bytes(snap.vm_available)} available)"
        )
    else:
        parts.append(f"VM {format_bytes(snap.vm_total)} (not running)")
    parts.append(f"instances using {format_bytes(snap.containers_used)}")

    running = [i for i in config.instances.values() if i.container_name in snap.containers]
    peaks = [estimated_peak(i, snap.vm_total) for i in running]
    overcommit = False
    if running and all(p is not None for p in peaks):
        total_peak = sum(peaks)
        overcommit = total_peak > snap.vm_total
        parts.append(
            f"est. peak {format_bytes(total_peak)} / {format_bytes(snap.vm_total)}" + ("  ⚠ overcommitted" if overcommit else "")
        )
    return " · ".join(parts), overcommit

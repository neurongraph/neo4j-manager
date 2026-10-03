"""Tests for memory sizing/stats and per-instance memory config. No Docker or Colima needed."""
from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from neo4j_manager import docker_ops, instance as inst, memory
from neo4j_manager.config import Config, Instance

GiB = 1024**3
MiB = 1024**2


def _instance(name="demo", **kw):
    return Instance(name=name, container_name=f"neo4j-{name}", **kw)


def _completed(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class TestSizes:
    @pytest.mark.parametrize("raw,expected", [("", ""), (" 2G ", "2g"), ("512m", "512m"), ("64K", "64k")])
    def test_normalize_valid(self, raw, expected):
        assert memory.normalize_size(raw) == expected

    @pytest.mark.parametrize("raw", ["2", "1.5g", "2gb", "lots", "-1g"])
    def test_normalize_invalid(self, raw):
        with pytest.raises(ValueError):
            memory.normalize_size(raw)

    @pytest.mark.parametrize(
        "raw,expected",
        [("2g", 2 * GiB), ("687.5MiB", int(687.5 * MiB)), ("7.738GiB", int(7.738 * GiB)), ("0B", 0), ("x", None)],
    )
    def test_parse(self, raw, expected):
        assert memory.parse_size(raw) == expected

    def test_format(self):
        assert memory.format_bytes(None) == "-"
        assert memory.format_bytes(512) == "512 B"
        assert memory.format_bytes(int(1.5 * MiB)) == "1.5 MiB"
        assert memory.format_bytes(8 * GiB) == "8.0 GiB"
        assert memory.format_bytes(2048 * GiB) == "2048.0 GiB"


class TestEstimatedPeak:
    def test_limit_wins(self):
        assert memory.estimated_peak(_instance(memory_limit="2g", heap_size="4g"), 8 * GiB) == 2 * GiB

    def test_defaults_use_quarter_of_vm(self):
        expected = 2 * GiB + memory.DEFAULT_PAGECACHE + memory.JVM_OVERHEAD
        assert memory.estimated_peak(_instance(), 8 * GiB) == expected

    def test_explicit_heap_and_pagecache(self):
        expected = 1 * GiB + 256 * MiB + memory.JVM_OVERHEAD
        assert memory.estimated_peak(_instance(heap_size="1g", pagecache_size="256m"), 8 * GiB) == expected

    def test_unknown_vm(self):
        assert memory.estimated_peak(_instance(), None) is None


class TestSnapshot:
    def test_collects_vm_and_containers(self):
        meminfo = "MemTotal:        8113632 kB\nMemFree:  100 kB\nMemAvailable:    6990352 kB\nHugePages_Total: 0\n"
        with (
            patch.object(docker_ops, "colima_vm_info", return_value={"status": "Running", "cpus": 4, "memory": 8 * GiB}),
            patch.object(docker_ops, "_run", side_effect=[
                _completed(meminfo),
                _completed("neo4j-a\t687.5MiB / 7.738GiB\nneo4j-b\t1GiB / 2GiB\n"),
            ]),
        ):
            snap = memory.snapshot(Config())
        assert snap.cpus == 4
        assert snap.vm_total == 8113632 * 1024
        assert snap.vm_available == 6990352 * 1024
        assert snap.containers == {"neo4j-a": (int(687.5 * MiB), int(7.738 * GiB)), "neo4j-b": (GiB, 2 * GiB)}
        assert snap.containers_used == int(687.5 * MiB) + GiB

    def test_vm_stopped(self):
        with (
            patch.object(docker_ops, "colima_vm_info", return_value={"status": "Stopped", "cpus": 2, "memory": 2 * GiB}),
            patch.object(docker_ops, "container_mem_usage", return_value={}),
            patch.object(docker_ops, "vm_meminfo") as meminfo,
        ):
            snap = memory.snapshot(Config())
        meminfo.assert_not_called()
        assert snap.vm_total == 2 * GiB and snap.vm_available is None

    def test_colima_list_picks_profile(self):
        out = '{"name":"other","memory":1}\n{"name":"default","status":"Running","cpus":4,"memory":8589934592}\n'
        with patch.object(docker_ops, "_run", return_value=_completed(out)):
            assert docker_ops.colima_vm_info("default")["memory"] == 8 * GiB
            assert docker_ops.colima_vm_info("missing") is None


class TestVmSummary:
    def _config(self, *instances):
        return Config(instances={i.name: i for i in instances})

    def test_overcommit_flagged(self):
        a, b = _instance("a"), _instance("b")
        # Each default instance may reach 3/4 GiB heap + 512M page cache + 512M overhead = 1.75 GiB.
        snap = memory.Snapshot("default", cpus=4, vm_total=3 * GiB, vm_available=GiB,
                               containers={"neo4j-a": (GiB, 3 * GiB), "neo4j-b": (GiB, 3 * GiB)})
        text, overcommit = memory.vm_summary(self._config(a, b), snap)
        assert overcommit
        assert "overcommitted" in text and "2.0 GiB / 3.0 GiB used" in text and "est. peak 3.5 GiB" in text

    def test_fits(self):
        a = _instance("a", memory_limit="2g")
        stopped = _instance("b")  # not running -> not counted
        snap = memory.Snapshot("default", vm_total=8 * GiB, vm_available=6 * GiB, containers={"neo4j-a": (GiB, 2 * GiB)})
        text, overcommit = memory.vm_summary(self._config(a, stopped), snap)
        assert not overcommit
        assert "est. peak 2.0 GiB / 8.0 GiB" in text


class TestDockerRun:
    def _cmd(self, instance, tmp_path):
        for attr in ("data_dir", "logs_dir", "import_dir", "plugins_dir"):
            setattr(instance, attr, str(tmp_path / attr))
        with patch.object(docker_ops, "_run", return_value=_completed()) as run:
            docker_ops.docker_run(instance)
        return run.call_args.args[0]

    def test_blank_memory_adds_nothing(self, tmp_path):
        cmd = self._cmd(_instance(), tmp_path)
        assert "--memory" not in cmd
        assert not any("server_memory" in c for c in cmd)

    def test_memory_settings_passed(self, tmp_path):
        cmd = self._cmd(_instance(heap_size="1g", pagecache_size="512m", memory_limit="2g"), tmp_path)
        assert cmd[cmd.index("--memory") + 1] == "2g"
        assert cmd.index("--memory") < cmd.index(_instance().image)
        assert "NEO4J_server_memory_heap_initial__size=1g" in cmd
        assert "NEO4J_server_memory_heap_max__size=1g" in cmd
        assert "NEO4J_server_memory_pagecache_size=512m" in cmd

    def test_stop_uses_grace_period(self):
        with patch.object(docker_ops, "_run", return_value=_completed()) as run:
            docker_ops.docker_stop("neo4j-demo")
        assert run.call_args.args[0] == ["docker", "stop", "-t", str(docker_ops.STOP_TIMEOUT), "neo4j-demo"]


class TestInstanceUpdate:
    def _update(self, instance, **kw):
        config = Config(instances={instance.name: instance})
        with (
            patch.object(inst.cfg, "load", return_value=config),
            patch.object(inst.cfg, "save"),
            patch.object(inst.docker_ops, "container_status", return_value="running"),
            patch.object(inst.docker_ops, "docker_stop") as stop,
            patch.object(inst.docker_ops, "docker_rm"),
            patch.object(inst.docker_ops, "docker_run") as run,
        ):
            result = inst.update(instance.name, **kw)
        return result, stop, run

    def test_memory_change_recreates_and_normalizes(self):
        result, stop, run = self._update(_instance(), heap_size="2G")
        assert result.heap_size == "2g"
        stop.assert_called_once()
        run.assert_called_once()

    def test_unchanged_memory_does_not_recreate(self):
        _, stop, run = self._update(_instance(heap_size="1g"), heap_size="1g", pagecache_size="", memory_limit="")
        stop.assert_not_called()
        run.assert_not_called()

    def test_invalid_size_rejected_before_touching_anything(self):
        with pytest.raises(ValueError):
            self._update(_instance(), memory_limit="huge")

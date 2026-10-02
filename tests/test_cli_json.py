"""Tests for the CLI's JSON output mode. No Docker or Colima needed."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from neo4j_manager.cli import app
from neo4j_manager.config import Instance
from neo4j_manager.docker_ops import DockerOpsError

runner = CliRunner()


def _make_instance(name="demo", bolt_port=7688, http_port=7475, password="secret123"):
    return Instance(
        name=name,
        container_name=f"neo4j-{name}",
        image="neo4j:latest",
        http_port=http_port,
        bolt_port=bolt_port,
        data_dir=f"/tmp/neo4j-data/{name}/data",
        auth_user="neo4j",
        auth_password=password,
    )


class TestCreateJson:
    def test_shape(self):
        instance = _make_instance()
        with (
            patch("neo4j_manager.cli.inst.create", return_value=instance),
            patch("neo4j_manager.cli.docker_ops.container_status", return_value="running"),
        ):
            result = runner.invoke(app, ["create", "demo", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["name"] == "demo"
        assert data["container_name"] == "neo4j-demo"
        assert data["bolt_url"] == "bolt://127.0.0.1:7688"
        assert data["http_url"] == "http://127.0.0.1:7475"
        assert data["bolt_port"] == 7688
        assert data["http_port"] == 7475
        assert data["user"] == "neo4j"
        assert data["password"] == "secret123"
        assert data["image"] == "neo4j:latest"
        assert data["state"] == "running"
        assert "data_dir" in data

    def test_duplicate_name_exits_nonzero_with_json_error(self):
        with patch(
            "neo4j_manager.cli.inst.create",
            side_effect=ValueError("Instance 'demo' already exists"),
        ):
            result = runner.invoke(app, ["create", "demo", "--json"])

        assert result.exit_code != 0
        # stderr (merged into output by test runner) should be a JSON error object
        err = json.loads(result.output)
        assert "error" in err
        assert "demo" in err["error"]

    def test_wait_with_no_start_is_usage_error(self):
        result = runner.invoke(app, ["create", "demo", "--json", "--wait", "--no-start"])

        assert result.exit_code != 0
        err = json.loads(result.output)
        assert "error" in err

    def test_wait_timeout_exits_nonzero(self):
        instance = _make_instance()
        with (
            patch("neo4j_manager.cli.inst.create", return_value=instance),
            patch(
                "neo4j_manager.cli.docker_ops.wait_ready",
                side_effect=DockerOpsError("Neo4j did not become ready within 1s"),
            ),
        ):
            result = runner.invoke(app, ["create", "demo", "--json", "--wait", "--timeout", "1"])

        assert result.exit_code != 0
        err = json.loads(result.output)
        assert "error" in err

    def test_wait_success_includes_state(self):
        instance = _make_instance()
        with (
            patch("neo4j_manager.cli.inst.create", return_value=instance),
            patch("neo4j_manager.cli.docker_ops.wait_ready"),
            patch("neo4j_manager.cli.docker_ops.container_status", return_value="running"),
        ):
            result = runner.invoke(app, ["create", "demo", "--json", "--wait"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["state"] == "running"
        assert data["bolt_url"].startswith("bolt://")


class TestStatusJson:
    def test_single_instance_shape(self):
        instance = _make_instance()
        mock_config = MagicMock()
        mock_config.instances = {"demo": instance}

        with (
            patch("neo4j_manager.cli.cfg_mod.load", return_value=mock_config),
            patch("neo4j_manager.cli.docker_ops.container_status", return_value="running"),
        ):
            result = runner.invoke(app, ["status", "demo", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["name"] == "demo"
        assert data["password"] == "secret123"
        assert data["state"] == "running"
        assert data["bolt_url"] == "bolt://127.0.0.1:7688"

    def test_unknown_instance_exits_nonzero_with_json_error(self):
        mock_config = MagicMock()
        mock_config.instances = {}

        with patch("neo4j_manager.cli.cfg_mod.load", return_value=mock_config):
            result = runner.invoke(app, ["status", "missing", "--json"])

        assert result.exit_code != 0
        err = json.loads(result.output)
        assert "error" in err

    def test_no_name_returns_array(self):
        instances = {
            "demo": _make_instance("demo"),
            "other": _make_instance("other", bolt_port=7689, http_port=7476),
        }
        mock_config = MagicMock()
        mock_config.instances = instances

        with (
            patch("neo4j_manager.cli.cfg_mod.load", return_value=mock_config),
            patch("neo4j_manager.cli.docker_ops.container_status", return_value="running"),
        ):
            result = runner.invoke(app, ["status", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2


class TestListJson:
    def test_array_shape_with_passwords(self):
        instances = {
            "demo": _make_instance("demo", password="pass1"),
            "other": _make_instance("other", bolt_port=7689, http_port=7476, password="pass2"),
        }
        mock_config = MagicMock()
        mock_config.instances = instances

        with (
            patch("neo4j_manager.cli.cfg_mod.load", return_value=mock_config),
            patch("neo4j_manager.cli.docker_ops.container_status", return_value="running"),
        ):
            result = runner.invoke(app, ["list", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2
        names = {d["name"] for d in data}
        assert names == {"demo", "other"}
        for item in data:
            assert "password" in item
            assert "bolt_url" in item
            assert item["bolt_url"].startswith("bolt://127.0.0.1:")

    def test_empty_list(self):
        mock_config = MagicMock()
        mock_config.instances = {}

        with patch("neo4j_manager.cli.cfg_mod.load", return_value=mock_config):
            result = runner.invoke(app, ["list", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == []

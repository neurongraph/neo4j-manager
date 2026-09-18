"""Free-port allocation for new instances, avoiding collisions with existing ones."""

from __future__ import annotations

import socket

from neo4j_manager.config import DEFAULT_BOLT_PORT, DEFAULT_HTTP_PORT, Config


def _is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def allocate_ports(config: Config) -> tuple[int, int]:
    used_http = {inst.http_port for inst in config.instances.values()}
    used_bolt = {inst.bolt_port for inst in config.instances.values()}

    http_port = DEFAULT_HTTP_PORT
    bolt_port = DEFAULT_BOLT_PORT
    while http_port in used_http or bolt_port in used_bolt or not _is_free(http_port) or not _is_free(bolt_port):
        http_port += 1
        bolt_port += 1
    return http_port, bolt_port

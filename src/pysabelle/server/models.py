
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServerInfo:
    name:     str
    host:     str
    port:     int
    password: str

    def __repr__(self) -> str:
        # Password intentionally redacted to avoid accidental exposure in logs.
        return (
            f"ServerInfo(name={self.name!r}, host={self.host!r}, "
            f"port={self.port}, password=***)"
        )

    def __str__(self) -> str:
        # Mirrors the wire format emitted by ``isabelle server``.
        return f'server "{self.name}" = {self.host}:{self.port}'
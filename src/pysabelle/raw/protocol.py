from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ReplyKind(str, Enum):
    OK       = "OK"
    ERROR    = "ERROR"
    FINISHED = "FINISHED"
    FAILED   = "FAILED"
    NOTE     = "NOTE"

_NAME_RE: re.Pattern[str] = re.compile(r"^([A-Za-z0-9_.]+)[ \t]*(.*)", re.DOTALL)


@dataclass
class RawReply:
    kind:         ReplyKind
    argument_raw: str

    def __post_init__(self) -> None:
        self.__json_cache: Any = _UNSET

    @classmethod
    def parse(cls, line: str) -> RawReply:
        line = line.rstrip("\r\n")
        if not line:
            raise ValueError("Cannot parse server reply: empty line.")

        m = _NAME_RE.match(line)
        if not m:
            raise ValueError(f"Cannot parse server reply: {line!r}")

        name_str, argument = m.group(1), m.group(2).strip()
        try:
            kind = ReplyKind(name_str)
        except ValueError:
            raise ValueError(
                f"Unknown reply kind {name_str!r} in: {line!r}"
            ) from None

        return cls(kind=kind, argument_raw=argument)

    def json(self) -> Any:
        if self.__json_cache is _UNSET:
            self.__json_cache = (
                json.loads(self.argument_raw) if self.argument_raw else None
            )
        return self.__json_cache

    def get(self, key: str, default: Any = None) -> Any:
        obj = self.json()
        if isinstance(obj, dict):
            return obj.get(key, default)
        return default

    @property
    def task_id(self) -> str | None:
        return self.get("task")  # type: ignore[return-value]

    @property
    def is_ok(self) -> bool:
        return self.kind is ReplyKind.OK

    @property
    def is_error(self) -> bool:
        return self.kind is ReplyKind.ERROR

    @property
    def is_finished(self) -> bool:
        return self.kind is ReplyKind.FINISHED

    @property
    def is_failed(self) -> bool:
        return self.kind is ReplyKind.FAILED

    @property
    def is_note(self) -> bool:
        return self.kind is ReplyKind.NOTE

    @property
    def is_terminal(self) -> bool:
        return self.kind in (ReplyKind.FINISHED, ReplyKind.FAILED)

    @property
    def is_async(self) -> bool:
        return self.kind in (ReplyKind.FINISHED, ReplyKind.FAILED, ReplyKind.NOTE)

    def __str__(self) -> str:
        arg = f" {self.argument_raw}" if self.argument_raw else ""
        return f"{self.kind.value}{arg}"

    def __repr__(self) -> str:
        arg = repr(self.argument_raw[:80]) if self.argument_raw else "''"
        return f"RawReply(kind={self.kind!r}, argument_raw={arg})"


class _Unset:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<UNSET>"


_UNSET = _Unset()


def encode_long_message(text: str) -> bytes:
    payload = text.encode("utf-8")
    header  = f"{len(payload)}\n".encode("ascii")
    return header + payload + b"\n"


def encode_short_message(text: str) -> bytes:
    if "\n" in text or "\r" in text:
        raise ValueError(
            f"Short message must not contain newline characters: {text!r}"
        )
    return (text + "\n").encode("utf-8")
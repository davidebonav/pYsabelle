from __future__ import annotations

import logging
from typing import Awaitable, Callable

from pysabelle.raw.protocol import RawReply
from pysabelle.raw.types import Message, NoteCallback, NodeWithStatus, TheoryProgress

log = logging.getLogger(__name__)

ProgressCallback = Callable[[TheoryProgress | Message], Awaitable[None]]
NodesStatusCallback = Callable[[list[NodeWithStatus]], Awaitable[None]]

def build_note_handler(on_progress: ProgressCallback) -> NoteCallback:
    async def _handler(reply: RawReply) -> None:
        payload = reply.json() or {}
        if not isinstance(payload, dict):
            return
        try:
            if TheoryProgress.is_theory_progress(payload):
                await on_progress(TheoryProgress.from_dict(payload))
            elif "message" in payload:
                await on_progress(Message.from_dict(payload))
        except Exception:
            log.exception("ProgressCallback raised during build NOTE.")

    return _handler


def use_theories_note_handler(
    on_progress:     ProgressCallback    | None,
    on_nodes_status: NodesStatusCallback | None,
) -> NoteCallback | None:
    if on_progress is None and on_nodes_status is None:
        return None

    async def _handler(reply: RawReply) -> None:
        payload = reply.json() or {}
        if not isinstance(payload, dict):
            return
        try:
            if on_nodes_status is not None and "nodes_status" in payload:
                nodes = [NodeWithStatus.from_dict(n) for n in payload["nodes_status"]]
                await on_nodes_status(nodes)
                return

            if on_progress is None:
                return

            if TheoryProgress.is_theory_progress(payload):
                await on_progress(TheoryProgress.from_dict(payload))
            elif "message" in payload:
                await on_progress(Message.from_dict(payload))

        except Exception:
            log.exception("Callback raised during use_theories NOTE.")

    return _handler

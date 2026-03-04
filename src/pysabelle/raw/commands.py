from __future__ import annotations

import logging
from typing import Any

from pysabelle.raw.dispatcher import TaskDispatcher
from pysabelle.raw.types import (
    NoteCallback,
    PurgeTheoriesArgs,
    PurgeTheoriesResults,
    SessionBuildArgs,
    SessionBuildResults,
    SessionId,
    SessionStartResult,
    SessionStopResult,
    TaskId,
    UseTheoriesArgs,
    UseTheoriesResults,
)

logger = logging.getLogger(__name__)


class RawCommands:
    def __init__(self, dispatcher: TaskDispatcher) -> None:
        self._d = dispatcher

    async def help(self) -> list[str]:
        reply = await self._d.run_sync("help")
        return reply.json() or []

    async def echo(self, value: Any) -> Any:
        reply = await self._d.run_sync("echo", value)
        return reply.json()

    async def shutdown(self) -> None:
        await self._d.run_sync("shutdown")

    async def cancel(self, task_id: TaskId) -> None:
        await self._d.run_sync("cancel", {"task": task_id.task})

    async def session_build(
        self,
        args:    SessionBuildArgs,
        on_note: NoteCallback | None = None,
    ) -> SessionBuildResults:
        final = await self._d.run_async("session_build", args.to_dict(), on_note=on_note)
        return SessionBuildResults.from_dict(final.json() or {})

    async def session_start(
        self,
        args:    SessionBuildArgs,
        on_note: NoteCallback | None = None,
    ) -> SessionStartResult:
        final = await self._d.run_async("session_start", args.to_dict(), on_note=on_note)
        return SessionStartResult.from_dict(final.json() or {})

    async def session_stop(
        self,
        session_id: SessionId,
        on_note:    NoteCallback | None = None,
    ) -> SessionStopResult:
        final = await self._d.run_async(
            "session_stop",
            {"session_id": session_id.session_id},
            on_note=on_note,
        )
        return SessionStopResult.from_dict(final.json() or {})

    async def use_theories(
        self,
        args:    UseTheoriesArgs,
        on_note: NoteCallback | None = None,
    ) -> UseTheoriesResults:
        final = await self._d.run_async("use_theories", args.to_dict(), on_note=on_note)
        return UseTheoriesResults.from_dict(final.json() or {})

    async def purge_theories(
        self,
        args: PurgeTheoriesArgs,
    ) -> PurgeTheoriesResults:
        reply = await self._d.run_sync("purge_theories", args.to_dict())
        return PurgeTheoriesResults.from_dict(reply.json() or {})
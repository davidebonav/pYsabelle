"""Asynchronous task dispatcher for multiplexing replies.

The dispatcher runs a background reader loop that receives raw replies from the
transport, classifies them (OK, ERROR, NOTE, FINISHED, FAILED) and routes them to
the appropriate waiting futures or callbacks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pysabelle.raw.exceptions import (
    IsabelleCommandError,
    IsabelleProtocolError,
    IsabelleTaskCancelled,
    IsabelleTimeoutError,
)
from pysabelle.raw.protocol import RawReply
from pysabelle.raw.transport import Transport
from pysabelle.raw.types import NoteCallback, UUID

logger = logging.getLogger(__name__)

#: Per-task registry entry: terminal Future paired with an optional NOTE callback.
_TaskEntry = tuple[asyncio.Future[RawReply], NoteCallback | None]


class TaskDispatcher:
    """Multiplexes incoming server replies to waiting callers.

    The dispatcher owns a background task that continuously reads from the transport.
    It maintains:
    - a single `_sync_reply` future for the next synchronous reply (OK/ERROR)
    - a dictionary `_pending` mapping task UUIDs to (future, callback) for async tasks

    Use `run_sync()` for commands that return a single immediate reply.
    Use `run_async()` for commands that first return an OK with a task ID, then later
    a FINISHED/FAILED, possibly with intermediate NOTE messages.

    Args:
        transport: Open transport to the server.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._pending: dict[UUID, _TaskEntry] = {}
        self._sync_reply: asyncio.Future[RawReply] | None = None
        self._cmd_lock = asyncio.Lock()
        self._loop_task: asyncio.Task[None] | None = None

    def _start(self) -> None:
        """Start the background reader loop if not already running."""
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.get_running_loop().create_task(
                self._reader_loop(), name="isabelle-dispatcher"
            )
            logger.debug("Reader loop started.")

    async def _stop(self) -> None:
        """Stop the reader loop and cancel all pending tasks."""
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            logger.debug("Reader loop stopped.")

        n_pending = len(self._pending)
        if n_pending:
            logger.warning("Shutting down with %d in-flight task(s) — cancelling.", n_pending)
        self._cancel_all(reason="dispatcher stopped")

    async def start(self) -> None:
        """Public API to start the dispatcher."""
        self._start()
        await asyncio.sleep(0)  # yield so the reader task is actually scheduled

    async def stop(self) -> None:
        """Public API to stop the dispatcher."""
        await self._stop()

    async def __aenter__(self) -> TaskDispatcher:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    async def _reader_loop(self) -> None:
        """Background task: read replies from transport and dispatch."""
        logger.debug("Reader loop running.")
        try:
            while True:
                reply = await self._transport.receive()
                logger.debug("Received: kind=%s task=%s", reply.kind.value, reply.task_id or "-")
                await self._dispatch(reply)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Reader loop crashed (%s: %s) — failing all pending futures.",
                type(exc).__name__, exc,
                exc_info=True,
            )
            self._fail_all(exc)

    async def _dispatch(self, reply: RawReply) -> None:
        """Route a single reply to the appropriate waiter."""
        if reply.is_ok or reply.is_error:
            # Synchronous reply
            if self._sync_reply is not None and not self._sync_reply.done():
                self._sync_reply.set_result(reply)
                self._sync_reply = None
            else:
                logger.warning(
                    "Received %s but no sync waiter is registered — dropping.",
                    reply.kind.value,
                )
            return

        # Intermediate notification (NOTE)
        if reply.is_note:
            task_id = reply.task_id
            entry = self._pending.get(task_id) if task_id else None
            if entry is None:
                logger.debug("NOTE for untracked task %r — dropping.", task_id)
                return
            _, on_note = entry
            if on_note is not None:
                try:
                    await on_note(reply)
                except Exception:
                    logger.exception("NOTE callback raised for task %s.", task_id)
            return

        # Terminal async reply (FINISHED / FAILED)
        if reply.is_terminal:
            task_id = reply.task_id
            entry = self._pending.pop(task_id, None) if task_id else None
            if entry is None:
                logger.warning(
                    "%s for untracked task %r — dropping.",
                    reply.kind.value, task_id,
                )
                return
            fut, _ = entry
            logger.debug(
                "Task %s %s. (%d task(s) still pending.)",
                task_id, reply.kind.value, len(self._pending),
            )
            if not fut.done():
                fut.set_result(reply)
            return

        logger.warning("Unroutable reply (kind=%s) — dropping.", reply.kind.value)

    def _fail_all(self, exc: Exception) -> None:
        """Fail all pending futures with the given exception."""
        if self._sync_reply and not self._sync_reply.done():
            self._sync_reply.set_exception(exc)
            self._sync_reply = None

        for task_id, (fut, _) in self._pending.items():
            if not fut.done():
                logger.debug("Failing task %s: %s", task_id, exc)
                fut.set_exception(exc)
        self._pending.clear()

    def _cancel_all(self, reason: str = "") -> None:
        """Cancel all pending futures."""
        if self._sync_reply and not self._sync_reply.done():
            self._sync_reply.cancel(reason)
            self._sync_reply = None

        for task_id, (fut, _) in self._pending.items():
            if not fut.done():
                logger.debug("Cancelling task %s: %s", task_id, reason)
                fut.cancel(reason)
        self._pending.clear()

    def _arm_sync_reply(self) -> asyncio.Future[RawReply]:
        """Prepare a future for the next synchronous reply."""
        fut = asyncio.get_running_loop().create_future()
        self._sync_reply = fut
        return fut

    def _register_task(
        self,
        task_id: UUID,
        on_note: NoteCallback | None,
    ) -> asyncio.Future[RawReply]:
        """Register a new async task and return a future for its terminal reply."""
        fut = asyncio.get_running_loop().create_future()
        self._pending[task_id] = (fut, on_note)
        logger.debug(
            "Registered async task %s. (%d task(s) now pending.)",
            task_id, len(self._pending),
        )
        return fut

    async def run_sync(
        self,
        command: str,
        arg: Any = None,
    ) -> RawReply:
        """Execute a synchronous command and return its immediate reply.

        The command is sent while holding the command lock. The reply must be
        either OK or ERROR; any other kind is considered a protocol error.

        Args:
            command: Name of the command (e.g., "help", "echo").
            arg: Optional JSON-serializable argument.

        Returns:
            The raw reply (OK or ERROR).

        Raises:
            IsabelleCommandError: If the server replies with ERROR.
            IsabelleProtocolError: If the reply is neither OK nor ERROR.
        """
        wire = f"{command} {json.dumps(arg, ensure_ascii=False)}" if arg is not None else command
        logger.debug("sync → %s", wire[:120])

        async with self._cmd_lock:
            sync_fut = self._arm_sync_reply()
            await self._transport.send(wire)
            reply = await sync_fut

        logger.debug("sync ← %s %s", reply.kind.value, (reply.argument_raw or "")[:120])

        if reply.is_error:
            raise IsabelleCommandError(
                kind=reply.kind.value,
                argument=reply.argument_raw,
                payload=reply.json(),
            )
        return reply

    async def run_async(
        self,
        command: str,
        arg: Any = None,
        on_note: NoteCallback | None = None,
        timeout: float | None = None,
    ) -> RawReply:
        """Execute an asynchronous command and wait for its terminal reply.

        The command is sent; the first reply must be OK containing a task ID.
        Subsequent NOTE messages are delivered to `on_note`, and the future
        returned completes when FINISHED or FAILED arrives.

        Args:
            command: Name of the command (e.g., "session_build").
            arg: Optional JSON-serializable argument.
            on_note: Callback invoked for each NOTE message belonging to this task.
            timeout: Maximum seconds to wait for the terminal reply.

        Returns:
            The terminal reply (FINISHED or FAILED).

        Raises:
            IsabelleCommandError: If the immediate reply is ERROR or the task fails.
            IsabelleProtocolError: If the immediate reply is OK but lacks a task ID.
            IsabelleTimeoutError: If the terminal reply does not arrive within `timeout`.
            IsabelleTaskCancelled: If the task ends with FAILED {"message": "Interrupt"}.
        """
        wire = f"{command} {json.dumps(arg, ensure_ascii=False)}" if arg is not None else command
        logger.debug("async → %s", wire[:120])

        # send + await immediate OK
        async with self._cmd_lock:
            sync_fut = self._arm_sync_reply()
            await self._transport.send(wire)
            immediate = await sync_fut

        if immediate.is_error:
            raise IsabelleCommandError(
                kind=immediate.kind.value,
                argument=immediate.argument_raw,
                payload=immediate.json(),
            )
        if not immediate.is_ok:
            raise IsabelleProtocolError(
                f"Expected OK for async command {command!r}, got {immediate.kind.value}."
            )

        task_id = immediate.task_id
        if not task_id:
            raise IsabelleProtocolError(
                f"Async command {command!r} returned OK without a task id: "
                f"{immediate.argument_raw!r}"
            )

        # register terminal future
        async with self._cmd_lock:
            terminal_fut = self._register_task(task_id, on_note)

        # await FINISHED / FAILED
        try:
            terminal: RawReply = await asyncio.wait_for(
                asyncio.shield(terminal_fut), timeout=timeout
            )
        except asyncio.TimeoutError:
            raise IsabelleTimeoutError(
                f"Task {task_id} timed out after {timeout}s", timeout=timeout
            ) from None

        logger.debug("Task %s → %s", task_id, terminal.kind.value)

        if terminal.is_failed:
            payload = terminal.json() or {}
            if payload.get("message") == "Interrupt":
                raise IsabelleTaskCancelled(f"Task {task_id} cancelled.")
            raise IsabelleCommandError(
                kind=terminal.kind.value,
                argument=terminal.argument_raw,
                payload=payload,
            )

        return terminal
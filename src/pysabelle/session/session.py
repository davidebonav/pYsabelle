from __future__ import annotations

import logging
from typing import Sequence

from pysabelle.client.client import IsabelleClient
from pysabelle.session.callbacks import (
    NodesStatusCallback,
    ProgressCallback,
    build_note_handler,
    use_theories_note_handler,
)
from pysabelle.session.exceptions import SessionAlreadyClosed, TheoryLoadError
from pysabelle.raw.types import (
    Message,
    NodeResultEntry,
    PurgeTheoriesArgs,
    PurgeTheoriesResults,
    SessionBuildArgs,
    SessionBuildResults,
    SessionId,
    SessionStopResult,
    UseTheoriesArgs,
    UseTheoriesResults,
)

log = logging.getLogger(__name__)


class IsabelleSession:
    def __init__(
        self,
        client:     IsabelleClient,
        session_id: SessionId,
        tmp_dir:    str,
    ) -> None:
        self._client     = client
        self._session_id = session_id
        self._tmp_dir    = tmp_dir
        self._closed     = False

    @property
    def session_id(self) -> SessionId:
        return self._session_id

    @property
    def tmp_dir(self) -> str:
        return self._tmp_dir

    @classmethod
    async def start(
        cls,
        session:          str,
        *,
        server_name:      str                      = "isabelle",
        reuse_server:     bool                     = True,
        options:          Sequence[str]            = (),
        dirs:             Sequence[str]            = (),
        include_sessions: Sequence[str]            = (),
        verbose:          bool                     = False,
        print_mode:       Sequence[str]            = (),
        on_progress:      ProgressCallback | None  = None,
        timeout:          float | None             = None,
    ) -> "IsabelleSession":
        client = await IsabelleClient.start(
            name=server_name, reuse_existing=reuse_server
        )
        return await cls._open_session(
            client,
            session=session,
            options=list(options),
            dirs=list(dirs),
            include_sessions=list(include_sessions),
            verbose=verbose,
            print_mode=list(print_mode),
            on_progress=on_progress,
            timeout=timeout,
        )

    @classmethod
    async def connect(
        cls,
        session:          str,
        *,
        host:             str                      = "127.0.0.1",
        port:             int                      = 4711,
        password:         str                      = "",
        options:          Sequence[str]            = (),
        dirs:             Sequence[str]            = (),
        include_sessions: Sequence[str]            = (),
        verbose:          bool                     = False,
        print_mode:       Sequence[str]            = (),
        on_progress:      ProgressCallback | None  = None,
        timeout:          float | None             = None,
    ) -> "IsabelleSession":
        client = await IsabelleClient.connect(
            host=host, port=port, password=password
        )
        return await cls._open_session(
            client,
            session=session,
            options=list(options),
            dirs=list(dirs),
            include_sessions=list(include_sessions),
            verbose=verbose,
            print_mode=list(print_mode),
            on_progress=on_progress,
            timeout=timeout,
        )

    @classmethod
    async def _open_session(
        cls,
        client:           IsabelleClient,
        *,
        session:          str,
        options:          list[str],
        dirs:             list[str],
        include_sessions: list[str],
        verbose:          bool,
        print_mode:       list[str],
        on_progress:      ProgressCallback | None,
        timeout:          float | None,
    ) -> "IsabelleSession":
        await client._dispatcher.start()

        args    = SessionBuildArgs(
            session=session,
            options=options,
            dirs=dirs,
            include_sessions=include_sessions,
            verbose=verbose,
            print_mode=print_mode,
        )
        on_note = build_note_handler(on_progress) if on_progress else None
        result  = await client.cmd.session_start(args, on_note=on_note)

        log.info(
            "Session '%s' started: id=%s tmp_dir=%s",
            session, result.session_id, result.tmp_dir,
        )
        return cls(client, result.session_id, result.tmp_dir)

    async def build(
        self,
        session:          str,
        *,
        options:          Sequence[str]            = (),
        dirs:             Sequence[str]            = (),
        include_sessions: Sequence[str]            = (),
        verbose:          bool                     = False,
        on_progress:      ProgressCallback | None  = None,
        timeout:          float | None             = None,
    ) -> SessionBuildResults:
        self._guard()
        args    = SessionBuildArgs(
            session=session,
            options=list(options),
            dirs=list(dirs),
            include_sessions=list(include_sessions),
            verbose=verbose,
        )
        on_note = build_note_handler(on_progress) if on_progress else None
        return await self._client.cmd.session_build(args, on_note=on_note)

    async def use_theories(
        self,
        theories:           list[str],
        *,
        master_dir:         str | None             = None,
        pretty_margin:      float | None           = None,
        unicode_symbols:    bool | None            = None,
        export_pattern:     str | None             = None,
        check_delay:        float | None           = None,
        check_limit:        int | None             = None,
        watchdog_timeout:   float | None           = None,
        nodes_status_delay: float | None           = None,
        on_progress:        ProgressCallback    | None = None,
        on_nodes_status:    NodesStatusCallback | None = None,
        raise_on_error:     bool                   = False,
        timeout:            float | None           = None,
    ) -> UseTheoriesResults:
        self._guard()
        args    = UseTheoriesArgs(
            session_id=self._session_id,
            theories=theories,
            master_dir=master_dir,
            pretty_margin=pretty_margin,
            unicode_symbols=unicode_symbols,
            export_pattern=export_pattern,
            check_delay=check_delay,
            check_limit=check_limit,
            watchdog_timeout=watchdog_timeout,
            nodes_status_delay=nodes_status_delay,
        )
        on_note  = use_theories_note_handler(on_progress, on_nodes_status)
        results  = await self._client.cmd.use_theories(args, on_note=on_note)

        if raise_on_error and not results.ok:
            raise TheoryLoadError(results.errors)

        return results

    async def purge_theories(
        self,
        theories:   Sequence[str] = (),
        *,
        master_dir: str | None    = None,
        all:        bool          = False,   # noqa: A002
    ) -> PurgeTheoriesResults:
        self._guard()
        args = PurgeTheoriesArgs(
            session_id=self._session_id,
            theories=list(theories),
            master_dir=master_dir,
            all=all,
        )
        return await self._client.cmd.purge_theories(args)

    async def check_theories(
        self,
        theories: list[str],
        **kwargs,
    ) -> list[Message]:
        results = await self.use_theories(theories, **kwargs)
        return results.errors

    async def load_and_purge(
        self,
        theories: list[str],
        **kwargs,
    ) -> UseTheoriesResults:
        results = await self.use_theories(theories, **kwargs)
        node_names = [entry.node.node_name for entry in results.nodes]
        if node_names:
            await self.purge_theories(node_names)
        return results

    async def close(self) -> SessionStopResult:
        if self._closed:
            return SessionStopResult(ok=True, return_code=0)

        self._closed = True

        result = await self._client.cmd.session_stop(self._session_id)
        log.info(
            "Session %s stopped: ok=%s rc=%d",
            self._session_id, result.ok, result.return_code,
        )

        await self._client._dispatcher.stop()
        await self._client._transport.close()

        if self._client._server is not None:
            self._client._server.stop()
            log.debug("Isabelle server process stopped.")

        return result

    async def __aenter__(self) -> "IsabelleSession":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    def __repr__(self) -> str:
        state = "closed" if self._closed else "open"
        return (
            f"IsabelleSession("
            f"session_id={self._session_id!r}, "
            f"tmp_dir={self._tmp_dir!r}, "
            f"state={state!r})"
        )

    def _guard(self) -> None:
        """Raise :exc:`~pysabelle.session.exceptions.SessionAlreadyClosed` if closed."""
        if self._closed:
            raise SessionAlreadyClosed()

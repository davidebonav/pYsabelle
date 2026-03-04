from __future__ import annotations

import logging

from pysabelle.server.server_process import IsabelleServerProcess, is_server_running
from pysabelle.raw.exceptions import IsabelleConnectionError
from pysabelle.raw.commands    import RawCommands
from pysabelle.raw.protocol    import ReplyKind
from pysabelle.raw.transport   import Transport
from pysabelle.raw.dispatcher  import TaskDispatcher

log = logging.getLogger(__name__)

class IsabelleClient:
    def __init__(
        self,
        transport:   Transport,
        dispatcher:  TaskDispatcher,
        commands:    RawCommands,
        *,
        server:      IsabelleServerProcess | None = None,
        default_timeout: float                        = 60.0,
    ) -> None:
        self._transport  = transport
        self._dispatcher = dispatcher
        self._server     = server
        self.cmd         = commands
        self._timeout   = default_timeout

    @classmethod
    async def connect(
        cls,
        host:     str,
        port:     int,
        password: str,
        *,
        _server:  IsabelleServerProcess | None = None,
    ) -> "IsabelleClient":
        transport  = await cls._open_transport(host, port, password)
        dispatcher = TaskDispatcher(transport)
        commands   = RawCommands(dispatcher)
        return cls(transport, dispatcher, commands, server=_server)

    @classmethod
    async def start(
        cls,
        name:           str  = "isabelle",
        reuse_existing: bool = True,
    ) -> "IsabelleClient":
        srv = cls._resolve_server(name, reuse_existing)
        srv.start()
        log.debug("Isabelle server running: host=%s port=%s", srv.info.host, srv.info.port)

        return await cls.connect(
            host     = srv.info.host,
            port     = srv.info.port,
            password = srv.info.password,
            _server  = srv,
        )

    @staticmethod
    def _resolve_server(name: str, reuse_existing: bool) -> IsabelleServerProcess:
        if reuse_existing and is_server_running(name=name):
            return IsabelleServerProcess(name=name, assume_existing=True)
        return IsabelleServerProcess(name=name, force_start=True)

    @staticmethod
    async def _open_transport(host: str, port: int, password: str) -> Transport:
        transport = await Transport.open(host, port)
        await transport.send(password, is_long_msg=False)

        reply = await transport.receive()
        if not reply.is_ok:
            await transport.close()
            raise IsabelleConnectionError(f"Handshake failed: {reply}")

        log.debug("Connected to Isabelle server: %s", reply.argument_raw)
        return transport

    async def stop_server(self) -> None:
        """Stop the managed server process, if any."""
        if self._server is not None:
            self._server.stop()
            log.debug("Isabelle server stopped.")

    def __getattr__(self, name: str):
        # Only reached if the attribute is not found on the instance itself.
        # At this point self.cmd is guaranteed to exist (set in __init__).
        try:
            return getattr(self.cmd, name)
        except AttributeError:
            raise AttributeError(
                f"'{type(self).__name__}' and 'RawCommands' have no attribute '{name}'"
            )

    async def __aenter__(self) -> "IsabelleClient":
        await self._dispatcher.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._dispatcher.stop()
        await self._transport.close()
        await self.stop_server()
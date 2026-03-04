"""Management of Isabelle server subprocesses."""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from pysabelle.server._patterns import SERVER_READY_RE
from pysabelle.server.exceptions import (
    IsabelleServerError,
    ServerAlreadyRunning,
    ServerNotRunning,
    ServerStartupTimeout,
)
from pysabelle.server.models import ServerInfo

logger = logging.getLogger(__name__)


def list_servers(isabelle_bin: str = "isabelle") -> list[ServerInfo]:
    """List all currently running Isabelle servers.

    Args:
        isabelle_bin: Path or name of the `isabelle` executable.

    Returns:
        List of ServerInfo objects for each running server.

    Raises:
        IsabelleServerError: If the `isabelle server -l` command fails.
    """
    try:
        result = subprocess.run(
            [isabelle_bin, "server", "-l"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise IsabelleServerError(
            f"'isabelle server -l' failed (rc={exc.returncode}): {exc.stderr.strip()}"
        ) from exc

    servers: list[ServerInfo] = []
    for line in result.stdout.splitlines():
        match = SERVER_READY_RE.match(line.strip())
        if match:
            servers.append(ServerInfo(
                name=match.group("name"),
                host=match.group("host"),
                port=int(match.group("port")),
                password=match.group("password"),
            ))

    logger.debug("Found %d running server(s).", len(servers))
    return servers


def is_server_running(name: str, isabelle_bin: str = "isabelle") -> bool:
    """Check whether a server with the given name is currently running.

    Args:
        name: Server name.
        isabelle_bin: Path or name of the `isabelle` executable.

    Returns:
        True if a server with that name exists.
    """
    return any(s.name == name for s in list_servers(isabelle_bin))


class BaseIsabelleServer(ABC):
    """Abstract interface for Isabelle server lifecycle managers."""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """``True`` if the server is currently active."""

    @property
    @abstractmethod
    def info(self) -> ServerInfo:
        """Connection details for the running server.

        Raises:
            ServerNotRunning: If the server has not been started yet.
        """

    @abstractmethod
    def start(self) -> ServerInfo:
        """Start (or attach to) the server and return its connection info."""

    @abstractmethod
    def stop(self) -> None:
        """Stop (or detach from) the server."""


class IsabelleServerProcess(BaseIsabelleServer):
    """Manages an `isabelle server` subprocess.

    This class can either spawn a new server, attach to an existing one,
    or replace a running server (force_start=True). It implements the
    context manager protocol.

    Args:
        name: Server name (passed as `-n`).
        port: Optional specific port; if None, the OS assigns one.
        log_file: Optional path to write server logs.
        assume_existing: If True, do not start a new process; instead,
            attach to an already running server with the given name.
        force_start: If True and a server with the same name is already
            running, stop it first and then start a fresh one.
        isabelle_bin: Path or name of the `isabelle` executable.
        startup_timeout: Maximum seconds to wait for the server to become ready.

    Raises:
        ValueError: If both `assume_existing` and `force_start` are True.
    """

    def __init__(
        self,
        name: str = "isabelle",
        port: Optional[int] = None,
        log_file: Optional[str | Path] = None,
        assume_existing: bool = False,
        force_start: bool = False,
        isabelle_bin: str = "isabelle",
        startup_timeout: float = 30.0,
    ) -> None:
        if assume_existing and force_start:
            raise ValueError(
                "'assume_existing' and 'force_start' are mutually exclusive."
            )

        self._name = name
        self._port = port
        self._log_file = Path(log_file) if log_file is not None else None
        self._assume_existing = assume_existing
        self._force_start = force_start
        self._isabelle_bin = isabelle_bin
        self._startup_timeout = startup_timeout

        self._info: Optional[ServerInfo] = None
        self._owns_process: bool = False

    @property
    def is_running(self) -> bool:
        """Check if the server process is still alive (by asking `isabelle server -l`)."""
        return is_server_running(self._name, self._isabelle_bin)

    @property
    def info(self) -> ServerInfo:
        """Connection information for the managed server.

        Raises:
            ServerNotRunning: If `start()` has not been called yet.
        """
        if self._info is None:
            raise ServerNotRunning(
                "No ServerInfo available — call start() first."
            )
        return self._info

    def start(self) -> ServerInfo:
        """Start or attach to the server.

        Behaviour depends on the constructor parameters:
        - assume_existing=True → attach to an existing server.
        - force_start=True → stop any existing server and start a new one.
        - otherwise → start a new server only if none is running.

        Returns:
            Connection information of the server.

        Raises:
            ServerAlreadyRunning: If a server is already running and neither
                assume_existing nor force_start are set.
            ServerNotRunning: If assume_existing is True but no server is running.
            ServerStartupTimeout: If the server does not become ready in time.
            IsabelleServerError: For other process‑related errors.
        """
        running_servers = list_servers(self._isabelle_bin)
        server_exists = any(s.name == self._name for s in running_servers)

        if self._assume_existing:
            return self._attach(server_exists, running_servers)

        if server_exists and self._force_start:
            self._stop_external(wait=True)
        elif server_exists:
            raise ServerAlreadyRunning(
                f"Server '{self._name}' is already running. "
                "Use assume_existing=True to attach or "
                "force_start=True to replace it."
            )

        return self._spawn()

    def stop(self) -> None:
        """Stop the server if we own it, otherwise just detach."""
        if not self._owns_process:
            logger.debug("Detaching from unowned server '%s'.", self._name)
            self._info = None
            return

        if not self.is_running:
            logger.debug("Server '%s' is already gone.", self._name)
            self._reset()
            return

        logger.debug("Stopping server '%s'.", self._name)
        subprocess.run(
            [self._isabelle_bin, "server", "-x", "-n", self._name],
            check=False,
            capture_output=True,
        )
        self._reset()

    def __enter__(self) -> IsabelleServerProcess:
        """Enter context: start the server."""
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> bool:
        """Exit context: stop the server."""
        self.stop()
        return False

    def __repr__(self) -> str:
        state = "running" if self._info is not None else "stopped"
        return (
            f"IsabelleServerProcess("
            f"name={self._name!r}, port={self._port!r}, state={state!r})"
        )

    def __str__(self) -> str:
        if self._info is not None:
            return f"IsabelleServerProcess '{self._name}' — {self._info}"
        return f"IsabelleServerProcess '{self._name}' — stopped"

    def _attach(
        self,
        server_exists: bool,
        running_servers: list[ServerInfo],
    ) -> ServerInfo:
        """Attach to an existing server."""
        if not server_exists:
            raise ServerNotRunning(
                f"assume_existing=True but server '{self._name}' is not running."
            )
        self._info = next(s for s in running_servers if s.name == self._name)
        self._owns_process = False
        logger.debug("Attached to existing server '%s': %s", self._name, self._info)
        return self._info

    def _spawn(self) -> ServerInfo:
        """Spawn a new server subprocess and wait for it to become ready."""
        cmd = self._build_command()
        logger.debug("Spawning: %s", " ".join(cmd))

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._owns_process = True

        self._info = self._wait_for_ready(proc)
        logger.debug("Server '%s' ready at %s:%d.", self._name, self._info.host, self._info.port)
        return self._info

    def _stop_external(self, *, wait: bool) -> None:
        """Stop a server that is not owned by this instance (for force_start)."""
        logger.debug("Stopping existing server '%s' before respawn.", self._name)
        subprocess.run(
            [self._isabelle_bin, "server", "-x", "-n", self._name],
            check=False,
            capture_output=True,
        )
        if not wait:
            return

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if not self.is_running:
                return
            time.sleep(0.2)

        raise IsabelleServerError(
            f"force_start: server '{self._name}' did not stop within 10 s."
        )

    def _build_command(self) -> list[str]:
        """Assemble the ``isabelle server`` command line from instance config."""
        cmd: list[str] = [self._isabelle_bin, "server", "-n", self._name]
        if self._port is not None:
            cmd += ["-p", str(self._port)]
        if self._log_file is not None:
            cmd += ["-L", str(self._log_file)]
        return cmd

    def _wait_for_ready(self, proc: subprocess.Popen[str]) -> ServerInfo:
        """Read the server's stdout until the ready line appears, then return info."""
        result_q: queue.Queue[ServerInfo | Exception] = queue.Queue()

        def _reader() -> None:
            try:
                for raw_line in proc.stdout:  # type: ignore[union-attr]
                    line = raw_line.strip()
                    logger.debug("Server stdout: %s", line)
                    match = SERVER_READY_RE.match(line)
                    if match:
                        result_q.put(ServerInfo(
                            name=match.group("name"),
                            host=match.group("host"),
                            port=int(match.group("port")),
                            password=match.group("password"),
                        ))
                        return
                rc = proc.wait()
                result_q.put(IsabelleServerError(
                    f"Server '{self._name}' exited (rc={rc}) before signalling readiness."
                ))
            except Exception as exc:  # noqa: BLE001
                result_q.put(exc)

        threading.Thread(target=_reader, daemon=True).start()

        try:
            outcome = result_q.get(timeout=self._startup_timeout)
        except queue.Empty:
            proc.kill()
            raise ServerStartupTimeout(
                f"Server '{self._name}' did not become ready "
                f"within {self._startup_timeout} s."
            )

        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def _reset(self) -> None:
        """Clear internal state after stopping."""
        self._info = None
        self._owns_process = False
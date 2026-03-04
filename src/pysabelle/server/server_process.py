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
    return any(s.name == name for s in list_servers(isabelle_bin))


class BaseIsabelleServer(ABC):
    """Interface for Isabelle server lifecycle managers."""

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
    def __init__(
        self,
        name:            str                  = "isabelle",
        port:            Optional[int]         = None,
        log_file:        Optional[str | Path]  = None,
        assume_existing: bool                  = False,
        force_start:     bool                  = False,
        isabelle_bin:    str                   = "isabelle",
        startup_timeout: float                 = 30.0,
    ) -> None:
        if assume_existing and force_start:
            raise ValueError(
                "'assume_existing' and 'force_start' are mutually exclusive."
            )

        self._name            = name
        self._port            = port
        self._log_file        = Path(log_file) if log_file is not None else None
        self._assume_existing = assume_existing
        self._force_start     = force_start
        self._isabelle_bin    = isabelle_bin
        self._startup_timeout = startup_timeout

        self._info:         Optional[ServerInfo] = None
        self._owns_process: bool                 = False

    @property
    def is_running(self) -> bool:
        return is_server_running(self._name, self._isabelle_bin)

    @property
    def info(self) -> ServerInfo:
        if self._info is None:
            raise ServerNotRunning(
                "No ServerInfo available — call start() first."
            )
        return self._info

    def start(self) -> ServerInfo:
        running_servers = list_servers(self._isabelle_bin)
        server_exists   = any(s.name == self._name for s in running_servers)

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
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val:  BaseException | None,
        exc_tb:   object,
    ) -> bool:
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
        server_exists:   bool,
        running_servers: list[ServerInfo],
    ) -> ServerInfo:
        if not server_exists:
            raise ServerNotRunning(
                f"assume_existing=True but server '{self._name}' is not running."
            )
        self._info         = next(s for s in running_servers if s.name == self._name)
        self._owns_process = False
        logger.debug("Attached to existing server '%s': %s", self._name, self._info)
        return self._info

    def _spawn(self) -> ServerInfo:
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
        self._info         = None
        self._owns_process = False
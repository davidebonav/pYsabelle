from __future__ import annotations


class IsabelleError(Exception):
    """Root exception for all Isabelle client errors."""


class IsabelleConnectionError(IsabelleError):
    """Raised on TCP-level failures: connection refused, EOF, or write error."""


class IsabelleProtocolError(IsabelleError):
    """Raised when a server message violates the §4.2 framing rules."""


class IsabelleCommandError(IsabelleError):
    """Raised when the server replies with ``ERROR`` or a task ends with ``FAILED``."""

    def __init__(self, kind: str, argument: str, payload: object = None) -> None:
        self.kind     = kind
        self.argument = argument
        self.payload  = payload
        super().__init__(f"{kind}: {argument or '(no message)'}")

class IsabelleTimeoutError(IsabelleError):
    """Raised when a task does not complete within the allotted time."""

    def __init__(self, task_id: str, timeout: float) -> None:
        self.task_id = task_id
        self.timeout = timeout
        super().__init__(f"Task {task_id} timed out after {timeout}s")

class IsabelleTaskCancelled(IsabelleError):
    """Raised when a task ends with ``FAILED {"message": "Interrupt"}``."""

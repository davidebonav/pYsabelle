"""Exceptions raised during server process management."""

from __future__ import annotations


class IsabelleError(Exception):
    """Root exception for the entire ``isabelle`` package."""


class IsabelleServerError(IsabelleError):
    """Base class for errors raised by server process management."""


class ServerStartupTimeout(IsabelleServerError):
    """The server did not emit its ready line within the configured timeout."""


class ServerAlreadyRunning(IsabelleServerError):
    """A server with the same name is already running."""


class ServerNotRunning(IsabelleServerError):
    """An operation requiring a live server was invoked with none active."""
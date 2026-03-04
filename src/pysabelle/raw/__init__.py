"""Raw protocol layer: transport, dispatcher, commands and types.

This package contains the low-level implementation of the Isabelle Server protocol.
All public symbols are re-exported here for convenience.
"""

from __future__ import annotations

from pysabelle.raw.exceptions import (
    IsabelleCommandError,
    IsabelleConnectionError,
    IsabelleError,
    IsabelleProtocolError,
    IsabelleTaskCancelled,
    IsabelleTimeoutError,
)

from pysabelle.raw.types import (
    UUID,
    NoteCallback,
    TaskId,
    SessionId,
    # §4.3 common
    Export,
    Message,
    MessageKind,
    Node,
    NodeResults,
    NodeResultEntry,
    NodeStatus,
    NodeWithStatus,
    Position,
    TheoryProgress,
    Timing,
    # §4.4.5 / §4.4.6
    SessionBuildArgs,
    SessionBuildResult,
    SessionBuildResults,
    SessionStartResult,
    # §4.4.7
    SessionStopResult,
    # §4.4.8
    UseTheoriesArgs,
    UseTheoriesResults,
    # §4.4.9
    PurgeTheoriesArgs,
    PurgeTheoriesResults,
)

from pysabelle.raw.protocol import (
    RawReply,
    ReplyKind,
    encode_long_message,
    encode_short_message,
)

from pysabelle.raw.transport import Transport

from pysabelle.raw.dispatcher import TaskDispatcher

from pysabelle.raw.commands import RawCommands

__all__ = [
    # exceptions
    "IsabelleError",
    "IsabelleConnectionError",
    "IsabelleProtocolError",
    "IsabelleCommandError",
    "IsabelleTaskCancelled",
    "IsabelleTimeoutError",
    # primitive aliases
    "UUID",
    "NoteCallback",
    "TaskId",
    "SessionId",
    # §4.3 common types
    "Position",
    "MessageKind",
    "Message",
    "TheoryProgress",
    "Timing",
    "NodeStatus",
    "Node",
    "NodeWithStatus",
    "Export",
    "NodeResults",
    "NodeResultEntry",
    # command arg / result types
    "SessionBuildArgs",
    "SessionBuildResult",
    "SessionBuildResults",
    "SessionStartResult",
    "SessionStopResult",
    "UseTheoriesArgs",
    "UseTheoriesResults",
    "PurgeTheoriesArgs",
    "PurgeTheoriesResults",
    # protocol
    "ReplyKind",
    "RawReply",
    "encode_long_message",
    "encode_short_message",
    # transport
    "Transport",
    # dispatcher
    "TaskDispatcher",
    # commands
    "RawCommands",
]
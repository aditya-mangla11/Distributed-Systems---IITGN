"""
Task 5 — Snapshot State Definitions

Topology-agnostic dataclasses for capturing process-local state and
assembling a consistent global snapshot (Chandy-Lamport algorithm).

Any distributed application (coordinator-worker, peer-to-peer, etc.)
simply serialises its own state into a dict and hands it to the
SnapshotManager; these classes handle the rest.
"""

from __future__ import annotations
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any


# ---------------------------------------------------------------------------
# Per-process local state
# ---------------------------------------------------------------------------

@dataclass
class ProcessState:
    """
    The local state of a single process at snapshot time.

    Attributes
    ----------
    process_id : str
        Unique identifier (e.g. "coordinator", "worker-0", "node-3").
    state_data : dict
        Arbitrary application-specific state as a plain dict.
        The application supplies this via its ``get_local_state()`` hook.
    timestamp : float
        Wall-clock time when the local state was captured.
    """
    process_id: str
    state_data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Channel state (messages in transit at snapshot time)
# ---------------------------------------------------------------------------

@dataclass
class ChannelState:
    """
    Messages recorded on a single directed channel between two processes.

    In the Chandy-Lamport algorithm, after a process records its own
    local state it begins recording incoming messages on each channel.
    Recording on a channel stops when a MARKER arrives on that channel.

    Attributes
    ----------
    from_process : str
        Sender process_id.
    to_process : str
        Receiver process_id.
    messages : list[dict]
        The messages captured in transit (each serialised as a dict).
    """
    from_process: str
    to_process: str
    messages: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Global snapshot
# ---------------------------------------------------------------------------

@dataclass
class GlobalSnapshot:
    """
    A consistent global snapshot assembled from local states and channel
    states reported by every process participating in the snapshot.

    Attributes
    ----------
    snapshot_id : str
        Unique identifier for this snapshot round.
    initiator_id : str
        The process that initiated the snapshot.
    process_states : dict[str, ProcessState]
        Mapping of process_id → ProcessState.
    channel_states : list[ChannelState]
        Messages recorded in transit on each channel.
    timestamp : float
        Time at which the snapshot was finalised (all reports received).
    """
    snapshot_id: str
    initiator_id: str
    process_states: dict[str, ProcessState] = field(default_factory=dict)
    channel_states: list[ChannelState] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    # --- Serialisation helpers (JSON-based for portability) -----------------

    def to_json(self) -> str:
        """Serialise the entire snapshot to a JSON string."""
        return json.dumps(asdict(self), indent=2)

    def to_bytes(self) -> bytes:
        """Serialise for writing to the DFS (bytes)."""
        return self.to_json().encode("utf-8")

    @classmethod
    def from_json(cls, raw: str) -> "GlobalSnapshot":
        """Reconstruct a GlobalSnapshot from a JSON string."""
        d = json.loads(raw)
        ps = {
            pid: ProcessState(**pdata)
            for pid, pdata in d.get("process_states", {}).items()
        }
        cs = [ChannelState(**cdata) for cdata in d.get("channel_states", [])]
        return cls(
            snapshot_id=d["snapshot_id"],
            initiator_id=d["initiator_id"],
            process_states=ps,
            channel_states=cs,
            timestamp=d.get("timestamp", 0.0),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "GlobalSnapshot":
        """Reconstruct from bytes read from the DFS."""
        return cls.from_json(data.decode("utf-8"))

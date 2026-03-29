"""
Task 5 — Snapshot Manager (Chandy-Lamport Algorithm)

Topology-agnostic implementation:  any process (coordinator, worker,
or peer) can instantiate a SnapshotManager to participate in or
initiate consistent global snapshots.

Usage
-----
1. Create a SnapshotManager, passing:
   - process_id       : unique name for this process
   - peer_addresses   : list of "host:port" for all OTHER processes
   - get_local_state  : callable returning a dict of application state
   - dfs_client       : (optional) ReplicatedAFSClientStub for storing snapshots

2. Register the SnapshotService on your gRPC server:
       snapshot_pb2_grpc.add_SnapshotServiceServicer_to_server(mgr, server)

3. To take a snapshot (from any process, typically periodically):
       mgr.initiate_snapshot()

4. On recovery:
       snapshot = mgr.recover_latest_snapshot()
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from typing import Callable, Optional

import grpc
from concurrent import futures

# Local proto imports
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import snapshot_pb2
import snapshot_pb2_grpc

from snapshot_state import ProcessState, ChannelState, GlobalSnapshot


class SnapshotManager(snapshot_pb2_grpc.SnapshotServiceServicer):
    """
    Implements both the initiator and participant roles of the
    Chandy-Lamport consistent snapshot algorithm over gRPC.

    Channel identifiers are process_ids (not addresses) throughout.
    The MARKER message carries the sender's process_id, which is used
    to close the right channel.
    """

    def __init__(
        self,
        process_id: str,
        peer_addresses: list[str],
        get_local_state: Callable[[], dict],
        dfs_client=None,
        snapshot_dir: str = "./snapshots_data",
    ):
        self.process_id = process_id
        self.peer_addresses = peer_addresses
        self.get_local_state = get_local_state
        self.dfs_client = dfs_client
        self.snapshot_dir = snapshot_dir
        os.makedirs(snapshot_dir, exist_ok=True)

        # Peer gRPC connections  { addr -> stub }
        self._peer_stubs: dict[str, snapshot_pb2_grpc.SnapshotServiceStub] = {}
        self._connect_peers()

        # Learnt mapping: process_id -> addr  (populated on first MARKER)
        self._pid_to_addr: dict[str, str] = {}

        # --- Per-snapshot mutable state (reset each round) ---
        self._lock = threading.Lock()
        self._active_snapshot_id: Optional[str] = None
        self._state_recorded: bool = False
        self._my_state: Optional[ProcessState] = None

        # Channel recording — keyed by the SENDER's process_id
        # After recording local state, a channel entry is created for
        # every peer.  Recording stops when a MARKER from that peer arrives.
        self._recording_channels: dict[str, list[dict]] = {}
        self._channels_closed: set[str] = set()           # set of process_ids
        self._all_peer_pids: set[str] = set()             # peer process_ids (learnt)

        # Initiator bookkeeping
        self._reports: dict[str, tuple] = {}               # pid -> (ProcessState, [ChannelState])
        self._expected_reporter_pids: set[str] = set()     # peer process_ids
        self._snapshot_complete_event = threading.Event()
        self._i_am_initiator: bool = False

    # ------------------------------------------------------------------
    # Peer connectivity
    # ------------------------------------------------------------------

    def _connect_peers(self):
        for addr in self.peer_addresses:
            try:
                ch = grpc.insecure_channel(addr)
                self._peer_stubs[addr] = snapshot_pb2_grpc.SnapshotServiceStub(ch)
            except Exception as e:
                print(f"[SNAPSHOT {self.process_id}] Cannot connect to {addr}: {e}")

    # ------------------------------------------------------------------
    # Initiator: start a new snapshot round
    # ------------------------------------------------------------------

    def initiate_snapshot(self, timeout: float = 30.0) -> Optional[GlobalSnapshot]:
        """
        Begin a new Chandy-Lamport snapshot round.

        Returns the assembled GlobalSnapshot, or None on failure.
        """
        snap_id = f"snap_{int(time.time())}_{uuid.uuid4().hex[:8]}"

        with self._lock:
            if self._active_snapshot_id is not None:
                print(f"[SNAPSHOT {self.process_id}] Snapshot already in progress.")
                return None
            self._reset_round(snap_id, is_initiator=True)

        print(f"[SNAPSHOT {self.process_id}] === Initiating snapshot {snap_id} ===")

        # Step 1: record own local state
        self._record_local_state()

        # Initiator immediately adds its own state to reports
        with self._lock:
            self._reports[self.process_id] = (self._my_state, [])

        # Step 2: send MARKER to all peers
        self._send_markers(snap_id)

        # Step 3: wait for all peers to report their state
        success = self._snapshot_complete_event.wait(timeout=timeout)
        if not success:
            print(f"[SNAPSHOT {self.process_id}] Timed out waiting for reports.")
            self._cleanup_round()
            return None

        # Step 4: assemble global snapshot
        snapshot = self._assemble_snapshot(snap_id)

        # Step 5: persist
        dfs_filename = self._persist_snapshot(snapshot)

        # Step 6: notify peers
        self._notify_complete(snap_id, dfs_filename)

        self._cleanup_round()
        print(f"[SNAPSHOT {self.process_id}] === Snapshot {snap_id} complete → {dfs_filename} ===")
        return snapshot

    # ------------------------------------------------------------------
    # gRPC Service: SendMarker  (participant receives a MARKER)
    # ------------------------------------------------------------------

    def SendMarker(self, request, context):
        snap_id = request.snapshot_id
        sender_pid = request.sender_id          # process_id of the sender

        with self._lock:
            first_marker = (self._active_snapshot_id != snap_id)
            if first_marker:
                self._reset_round(snap_id, is_initiator=False)

        if first_marker:
            print(f"[SNAPSHOT {self.process_id}] First MARKER from {sender_pid} "
                  f"for {snap_id}. Recording local state.")
            self._record_local_state()
            self._send_markers(snap_id)

        # Close the channel FROM sender_pid
        with self._lock:
            self._channels_closed.add(sender_pid)

        # If all channels are closed, send our report
        self._maybe_send_report(snap_id)

        return snapshot_pb2.MarkerResponse(success=True)

    # ------------------------------------------------------------------
    # gRPC Service: ReportLocalState  (initiator receives a report)
    # ------------------------------------------------------------------

    def ReportLocalState(self, request, context):
        reporter_pid = request.process_id

        # Only the initiator cares about reports
        with self._lock:
            if not self._i_am_initiator:
                return snapshot_pb2.ReportResponse(success=True)

        pstate = ProcessState(
            process_id=reporter_pid,
            state_data=json.loads(request.serialized_state),
            timestamp=time.time(),
        )
        cstates = [
            ChannelState(**cs) for cs in json.loads(request.channel_states)
        ]

        with self._lock:
            self._reports[reporter_pid] = (pstate, cstates)
            received = len(self._reports)
            # We need reports from all peers + our own = len(peer_addresses) + 1
            expected = len(self.peer_addresses) + 1
            print(f"[SNAPSHOT {self.process_id}] Received report from "
                  f"{reporter_pid} ({received}/{expected})")

            if received >= expected:
                self._snapshot_complete_event.set()

        return snapshot_pb2.ReportResponse(success=True)

    # ------------------------------------------------------------------
    # gRPC Service: SnapshotComplete
    # ------------------------------------------------------------------

    def SnapshotComplete(self, request, context):
        print(f"[SNAPSHOT {self.process_id}] Snapshot {request.snapshot_id} "
              f"saved to {request.dfs_filename}")
        self._cleanup_round()
        return snapshot_pb2.SnapshotCompleteAck(success=True)

    # ------------------------------------------------------------------
    # Application integration: record incoming messages
    # ------------------------------------------------------------------

    def record_incoming_message(self, from_process: str, message: dict):
        """
        Called by the application whenever it receives a message from
        another process.  If we are currently recording the channel
        from ``from_process``, the message is captured.
        """
        with self._lock:
            if (self._active_snapshot_id is not None
                    and self._state_recorded
                    and from_process not in self._channels_closed
                    and from_process in self._recording_channels):
                self._recording_channels[from_process].append(message)

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def recover_latest_snapshot(self) -> Optional[GlobalSnapshot]:
        """Load the most recent snapshot (DFS first, then local disk)."""
        if self.dfs_client:
            snap = self._recover_from_dfs()
            if snap:
                return snap
        return self._recover_from_disk()

    def _recover_from_dfs(self) -> Optional[GlobalSnapshot]:
        try:
            handle = self.dfs_client.open("latest_snapshot.json", mode="r")
            data = self.dfs_client.read(handle, 0, 10 * 1024 * 1024)
            self.dfs_client.close(handle)
            if data:
                return GlobalSnapshot.from_bytes(data)
        except Exception as e:
            print(f"[SNAPSHOT {self.process_id}] DFS recovery failed: {e}")
        return None

    def _recover_from_disk(self) -> Optional[GlobalSnapshot]:
        files = sorted(
            [f for f in os.listdir(self.snapshot_dir) if f.endswith(".json")],
            reverse=True,
        )
        if not files:
            return None
        path = os.path.join(self.snapshot_dir, files[0])
        with open(path, "r") as f:
            return GlobalSnapshot.from_json(f.read())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_round(self, snap_id: str, is_initiator: bool):
        """Prepare mutable state for a new snapshot round.  MUST hold _lock."""
        self._active_snapshot_id = snap_id
        self._i_am_initiator = is_initiator
        self._state_recorded = False
        self._my_state = None
        # We don't know peer process_ids yet (they arrive with markers/reports)
        # so channels are created lazily.
        self._recording_channels = {}
        self._channels_closed = set()
        self._all_peer_pids = set()
        self._reports = {}
        self._expected_reporter_pids = set()
        self._snapshot_complete_event = threading.Event()

    def _cleanup_round(self):
        with self._lock:
            self._active_snapshot_id = None
            self._state_recorded = False
            self._i_am_initiator = False

    def _record_local_state(self):
        """Invoke the application hook and save our local state."""
        state_data = self.get_local_state()
        with self._lock:
            self._my_state = ProcessState(
                process_id=self.process_id,
                state_data=state_data,
                timestamp=time.time(),
            )
            self._state_recorded = True

    def _send_markers(self, snap_id: str):
        """Send a MARKER to every peer (identified by address)."""
        marker = snapshot_pb2.MarkerMessage(
            snapshot_id=snap_id,
            sender_id=self.process_id,
        )
        for addr, stub in self._peer_stubs.items():
            try:
                stub.SendMarker(marker, timeout=5.0)
            except Exception as e:
                print(f"[SNAPSHOT {self.process_id}] MARKER to {addr} failed: {e}")

    def _maybe_send_report(self, snap_id: str):
        """
        After all incoming channels are closed, build and send the
        local-state report.

        For the initiator: store the report locally.
        For participants: send report to ALL peers (only the initiator
        will act on it).
        """
        with self._lock:
            # The sender_id in each MARKER is a process_id.
            # We need MARKERs from all peers.  We know the number of
            # peers from peer_addresses.
            if len(self._channels_closed) < len(self.peer_addresses):
                return

            cstates = self._build_channel_states()

            if self._i_am_initiator:
                self._reports[self.process_id] = (self._my_state, cstates)
                # Check completion: we need reports from every peer process
                # plus our own.
                if len(self._reports) >= len(self.peer_addresses) + 1:
                    self._snapshot_complete_event.set()
                return

            my_state = self._my_state

        # Non-initiator: send report to all peers
        report = snapshot_pb2.LocalStateReport(
            snapshot_id=snap_id,
            process_id=self.process_id,
            serialized_state=json.dumps(my_state.state_data).encode(),
            channel_states=json.dumps(
                [{"from_process": cs.from_process,
                  "to_process": cs.to_process,
                  "messages": cs.messages} for cs in cstates]
            ).encode(),
        )
        for addr, stub in self._peer_stubs.items():
            try:
                stub.ReportLocalState(report, timeout=5.0)
            except Exception:
                pass

    def _build_channel_states(self) -> list[ChannelState]:
        result = []
        for pid, msgs in self._recording_channels.items():
            if msgs:
                result.append(ChannelState(
                    from_process=pid,
                    to_process=self.process_id,
                    messages=msgs,
                ))
        return result

    def _assemble_snapshot(self, snap_id: str) -> GlobalSnapshot:
        with self._lock:
            process_states = {}
            channel_states = []

            if self._my_state:
                process_states[self.process_id] = self._my_state

            for pid, (pstate, cstates) in self._reports.items():
                if pid != self.process_id:  # Don't double-add initiator
                    process_states[pid] = pstate
                channel_states.extend(cstates)

        return GlobalSnapshot(
            snapshot_id=snap_id,
            initiator_id=self.process_id,
            process_states=process_states,
            channel_states=channel_states,
            timestamp=time.time(),
        )

    def _persist_snapshot(self, snapshot: GlobalSnapshot) -> str:
        filename = f"snapshot_{snapshot.snapshot_id}.json"
        data = snapshot.to_bytes()

        local_path = os.path.join(self.snapshot_dir, filename)
        with open(local_path, "wb") as f:
            f.write(data)

        if self.dfs_client:
            try:
                dfs_filename = f"snapshot_{snapshot.snapshot_id}.json"
                self.dfs_client.create(dfs_filename)
                handle = self.dfs_client.open(dfs_filename, mode="w")
                self.dfs_client.write(handle, 0, data)
                self.dfs_client.close(handle)

                try:
                    self.dfs_client.create("latest_snapshot.json")
                except Exception:
                    pass
                handle = self.dfs_client.open("latest_snapshot.json", mode="w")
                self.dfs_client.write(handle, 0, data)
                self.dfs_client.close(handle)

                print(f"[SNAPSHOT {self.process_id}] Saved to DFS: {dfs_filename}")
                return dfs_filename
            except Exception as e:
                print(f"[SNAPSHOT {self.process_id}] DFS save failed: {e}")

        return local_path

    def _notify_complete(self, snap_id: str, dfs_filename: str):
        msg = snapshot_pb2.SnapshotCompleteMsg(
            snapshot_id=snap_id,
            dfs_filename=dfs_filename,
        )
        for addr, stub in self._peer_stubs.items():
            try:
                stub.SnapshotComplete(msg, timeout=5.0)
            except Exception:
                pass

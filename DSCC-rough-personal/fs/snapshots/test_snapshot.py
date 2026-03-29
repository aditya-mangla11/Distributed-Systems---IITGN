#!/usr/bin/env python3
"""
Task 5 — Snapshot Module Tests

Tests cover:
  1. Serialisation round-trip (ProcessState, ChannelState, GlobalSnapshot)
  2. Snapshot file persistence to local disk
  3. Recovery from local disk
  4. Multi-process snapshot protocol (3 gRPC processes in threads)
  5. Recovery after simulated worker failure

Run:
    python3 test_snapshot.py
"""

import os
import sys
import json
import time
import shutil
import threading
import unittest

import grpc
from concurrent import futures

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import snapshot_pb2_grpc
from snapshot_state import ProcessState, ChannelState, GlobalSnapshot
from snapshot_manager import SnapshotManager

TEST_SNAP_DIR = os.path.join(SCRIPT_DIR, "_test_snapshots")
PORTS = [61010, 61011, 61012]
ADDRS = [f"localhost:{p}" for p in PORTS]


class TestSnapshotState(unittest.TestCase):
    """Tests for the data classes and their serialisation."""

    def test_process_state_roundtrip(self):
        ps = ProcessState(
            process_id="worker-0",
            state_data={"chunk": "A", "progress": 42, "primes": [2, 3, 5]},
        )
        d = json.loads(json.dumps({
            "process_id": ps.process_id,
            "state_data": ps.state_data,
            "timestamp": ps.timestamp,
        }))
        restored = ProcessState(**d)
        self.assertEqual(restored.process_id, "worker-0")
        self.assertEqual(restored.state_data["progress"], 42)

    def test_channel_state_roundtrip(self):
        cs = ChannelState(
            from_process="worker-0",
            to_process="coordinator",
            messages=[{"type": "result", "value": 7}],
        )
        d = json.loads(json.dumps({
            "from_process": cs.from_process,
            "to_process": cs.to_process,
            "messages": cs.messages,
        }))
        restored = ChannelState(**d)
        self.assertEqual(len(restored.messages), 1)
        self.assertEqual(restored.messages[0]["value"], 7)

    def test_global_snapshot_roundtrip(self):
        snap = GlobalSnapshot(
            snapshot_id="test-001",
            initiator_id="coordinator",
            process_states={
                "coordinator": ProcessState("coordinator", {"assigned": [1, 2]}),
                "worker-0": ProcessState("worker-0", {"progress": 10}),
            },
            channel_states=[
                ChannelState("worker-0", "coordinator", [{"primes": [7, 11]}]),
            ],
        )
        json_str = snap.to_json()
        restored = GlobalSnapshot.from_json(json_str)
        self.assertEqual(restored.snapshot_id, "test-001")
        self.assertEqual(restored.initiator_id, "coordinator")
        self.assertIn("coordinator", restored.process_states)
        self.assertIn("worker-0", restored.process_states)
        self.assertEqual(
            restored.process_states["worker-0"].state_data["progress"], 10
        )
        self.assertEqual(len(restored.channel_states), 1)

    def test_bytes_roundtrip(self):
        snap = GlobalSnapshot(
            snapshot_id="bytes-test",
            initiator_id="node-0",
            process_states={
                "node-0": ProcessState("node-0", {"val": 99}),
            },
        )
        data = snap.to_bytes()
        restored = GlobalSnapshot.from_bytes(data)
        self.assertEqual(restored.snapshot_id, "bytes-test")
        self.assertEqual(
            restored.process_states["node-0"].state_data["val"], 99
        )


class TestSnapshotPersistence(unittest.TestCase):
    """Tests for local-disk save & recovery."""

    def setUp(self):
        if os.path.exists(TEST_SNAP_DIR):
            shutil.rmtree(TEST_SNAP_DIR)
        os.makedirs(TEST_SNAP_DIR, exist_ok=True)

    def tearDown(self):
        if os.path.exists(TEST_SNAP_DIR):
            shutil.rmtree(TEST_SNAP_DIR)

    def test_save_and_recover(self):
        snap = GlobalSnapshot(
            snapshot_id="persist-001",
            initiator_id="node-0",
            process_states={
                "node-0": ProcessState("node-0", {"x": 42}),
            },
        )
        # Save
        path = os.path.join(TEST_SNAP_DIR, f"snapshot_{snap.snapshot_id}.json")
        with open(path, "wb") as f:
            f.write(snap.to_bytes())

        # Recover using SnapshotManager
        mgr = SnapshotManager(
            process_id="node-0",
            peer_addresses=[],
            get_local_state=lambda: {},
            snapshot_dir=TEST_SNAP_DIR,
        )
        recovered = mgr.recover_latest_snapshot()
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.snapshot_id, "persist-001")
        self.assertEqual(
            recovered.process_states["node-0"].state_data["x"], 42
        )

    def test_recover_latest_of_many(self):
        # Save two snapshots — recovery should return the latest (by sort)
        for sid in ["snap_a", "snap_z"]:
            snap = GlobalSnapshot(
                snapshot_id=sid,
                initiator_id="node-0",
                process_states={
                    "node-0": ProcessState("node-0", {"id": sid}),
                },
            )
            path = os.path.join(TEST_SNAP_DIR, f"snapshot_{sid}.json")
            with open(path, "wb") as f:
                f.write(snap.to_bytes())

        mgr = SnapshotManager(
            process_id="node-0",
            peer_addresses=[],
            get_local_state=lambda: {},
            snapshot_dir=TEST_SNAP_DIR,
        )
        recovered = mgr.recover_latest_snapshot()
        # "snap_z" sorts after "snap_a", so it's "latest"
        self.assertEqual(recovered.snapshot_id, "snap_z")


class TestSnapshotProtocol(unittest.TestCase):
    """
    End-to-end test: 3 mock processes each running a gRPC server
    with the SnapshotService.  Process 0 initiates a snapshot.
    """

    def setUp(self):
        # Clean up
        for port in PORTS:
            d = os.path.join(SCRIPT_DIR, f"_test_snap_{port}")
            if os.path.exists(d):
                shutil.rmtree(d)

        self.servers = []
        self.managers = []

        # Application state per process (simulated)
        self.app_states = [
            {"chunk": "A", "progress": 10, "primes": [2, 3]},
            {"chunk": "B", "progress": 20, "primes": [5, 7]},
            {"chunk": "C", "progress": 30, "primes": [11, 13]},
        ]

        for i, port in enumerate(PORTS):
            peers = [a for a in ADDRS if a != f"localhost:{port}"]
            snap_dir = os.path.join(SCRIPT_DIR, f"_test_snap_{port}")

            state_idx = i  # capture for closure
            mgr = SnapshotManager(
                process_id=f"proc-{i}",
                peer_addresses=peers,
                get_local_state=lambda idx=state_idx: dict(self.app_states[idx]),
                snapshot_dir=snap_dir,
            )
            self.managers.append(mgr)

            server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
            snapshot_pb2_grpc.add_SnapshotServiceServicer_to_server(mgr, server)
            server.add_insecure_port(f"[::]:{port}")
            server.start()
            self.servers.append(server)

        # Give servers time to start
        time.sleep(1)

    def tearDown(self):
        for s in self.servers:
            s.stop(grace=1)
        for port in PORTS:
            d = os.path.join(SCRIPT_DIR, f"_test_snap_{port}")
            if os.path.exists(d):
                shutil.rmtree(d)

    def test_snapshot_captures_all_states(self):
        """Initiator (proc-0) should capture state from all 3 processes."""
        snapshot = self.managers[0].initiate_snapshot(timeout=15)
        self.assertIsNotNone(snapshot, "Snapshot should not be None")
        self.assertIn("proc-0", snapshot.process_states)
        self.assertIn("proc-1", snapshot.process_states)
        self.assertIn("proc-2", snapshot.process_states)

        # Verify captured data matches original state
        self.assertEqual(
            snapshot.process_states["proc-0"].state_data["progress"], 10
        )
        self.assertEqual(
            snapshot.process_states["proc-1"].state_data["primes"], [5, 7]
        )
        self.assertEqual(
            snapshot.process_states["proc-2"].state_data["chunk"], "C"
        )

    def test_snapshot_persists_locally(self):
        """Snapshot should be saved to the initiator's local disk."""
        snapshot = self.managers[0].initiate_snapshot(timeout=15)
        self.assertIsNotNone(snapshot)

        # Check that a file was written
        snap_dir = os.path.join(SCRIPT_DIR, f"_test_snap_{PORTS[0]}")
        files = [f for f in os.listdir(snap_dir) if f.endswith(".json")]
        self.assertGreater(len(files), 0, "Snapshot file should exist")

    def test_recovery_from_disk(self):
        """After taking a snapshot, recovery should restore it."""
        snapshot = self.managers[0].initiate_snapshot(timeout=15)
        self.assertIsNotNone(snapshot)

        recovered = self.managers[0].recover_latest_snapshot()
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.snapshot_id, snapshot.snapshot_id)
        self.assertEqual(
            set(recovered.process_states.keys()),
            set(snapshot.process_states.keys()),
        )

    def test_simulated_failure_recovery(self):
        """
        Take a snapshot, simulate a worker failure (lose state),
        then recover from the snapshot.
        """
        # Take snapshot
        snapshot = self.managers[0].initiate_snapshot(timeout=15)
        self.assertIsNotNone(snapshot)

        # Simulate worker-1 crashing (wipe its state)
        self.app_states[1] = {"chunk": "LOST", "progress": 0, "primes": []}

        # Recover
        recovered = self.managers[0].recover_latest_snapshot()
        self.assertIsNotNone(recovered)

        # Worker-1's saved state should be from before the crash
        w1_state = recovered.process_states["proc-1"].state_data
        self.assertEqual(w1_state["progress"], 20)
        self.assertEqual(w1_state["primes"], [5, 7])
        self.assertNotEqual(w1_state["chunk"], "LOST")


if __name__ == "__main__":
    unittest.main(verbosity=2)

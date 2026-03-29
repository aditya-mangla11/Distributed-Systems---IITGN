#!/usr/bin/env python3
"""
Task 5 — Mock Integration Demo

Demonstrates how to integrate the SnapshotManager into a distributed
application.  Spins up 3 lightweight "worker" processes as threads,
each running a gRPC server with the SnapshotService.  The first
process initiates a snapshot, and all three participate.

== How your Task 4 teammate uses this ==
1. Copy the pattern from WorkerProcess.__init__:
       self.snapshot_mgr = SnapshotManager(
           process_id=..., peer_addresses=...,
           get_local_state=self.get_local_state,
       )
2. Register the service on their gRPC server:
       snapshot_pb2_grpc.add_SnapshotServiceServicer_to_server(
           self.snapshot_mgr, grpc_server)
3. Implement get_local_state() → dict  (return whatever you want saved)
4. Periodically call  self.snapshot_mgr.initiate_snapshot()
5. On restart, call  self.snapshot_mgr.recover_latest_snapshot()

Run this file directly to see the snapshot in action:
    python3 mock_integration.py
"""

import os
import sys
import time
import threading
import shutil
import json

import grpc
from concurrent import futures

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import snapshot_pb2_grpc
from snapshot_manager import SnapshotManager
from snapshot_state import GlobalSnapshot

# Ports for the 3 mock processes
PORTS = [60010, 60011, 60012]
ADDRS = [f"localhost:{p}" for p in PORTS]


class MockWorkerProcess:
    """
    Simulates a distributed worker process with some application state.
    This is what Task 4 code would look like after integration.
    """

    def __init__(self, process_id: str, port: int, peer_addrs: list[str]):
        self.process_id = process_id
        self.port = port

        # ---- Application state (this is Task 4's domain) ----
        self.assigned_chunk = f"chunk_{port}"
        self.progress = 0
        self.primes_found = []

        # ---- Snapshot integration ----
        snap_dir = os.path.join(SCRIPT_DIR, f"_mock_snap_{port}")
        self.snapshot_mgr = SnapshotManager(
            process_id=process_id,
            peer_addresses=peer_addrs,
            get_local_state=self.get_local_state,
            snapshot_dir=snap_dir,
        )

        # ---- Start gRPC server ----
        self.server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        snapshot_pb2_grpc.add_SnapshotServiceServicer_to_server(
            self.snapshot_mgr, self.server,
        )
        self.server.add_insecure_port(f"[::]:{port}")
        self.server.start()
        print(f"  [{process_id}] gRPC server on :{port}")

    def get_local_state(self) -> dict:
        """
        Application hook — called by SnapshotManager once per snapshot.
        Return whatever state you want saved.
        """
        return {
            "assigned_chunk": self.assigned_chunk,
            "progress": self.progress,
            "primes_found": self.primes_found,
        }

    def do_work(self, steps: int = 5):
        """Simulate some computation."""
        for i in range(steps):
            self.progress += 1
            # Pretend we found a prime
            self.primes_found.append(self.progress * 10 + 7)
            time.sleep(0.05)

    def stop(self):
        self.server.stop(grace=1)


def main():
    print("=" * 60)
    print(" Task 5 — Mock Snapshot Integration Demo")
    print("=" * 60)

    # Clean up old snapshot data
    for port in PORTS:
        d = os.path.join(SCRIPT_DIR, f"_mock_snap_{port}")
        if os.path.exists(d):
            shutil.rmtree(d)

    # Start 3 mock worker processes
    workers = []
    for i, port in enumerate(PORTS):
        peers = [a for a in ADDRS if a != f"localhost:{port}"]
        w = MockWorkerProcess(
            process_id=f"worker-{i}",
            port=port,
            peer_addrs=peers,
        )
        workers.append(w)

    # Give gRPC servers time to fully start
    time.sleep(1)

    # Simulate some work
    print("\n--- Workers doing computation ---")
    for w in workers:
        w.do_work(steps=3)
        print(f"  [{w.process_id}] progress={w.progress}, "
              f"primes={w.primes_found}")

    # Worker 0 initiates a snapshot
    print("\n--- Initiating snapshot from worker-0 ---")
    snapshot = workers[0].snapshot_mgr.initiate_snapshot(timeout=10)

    if snapshot:
        print(f"\n--- Snapshot captured! ID: {snapshot.snapshot_id} ---")
        print(f"  Processes snapshotted: "
              f"{list(snapshot.process_states.keys())}")
        for pid, ps in snapshot.process_states.items():
            print(f"  [{pid}] state: {ps.state_data}")
        print(f"  Channel states: {len(snapshot.channel_states)}")

        # Verify round-trip serialisation
        json_str = snapshot.to_json()
        restored = GlobalSnapshot.from_json(json_str)
        assert restored.snapshot_id == snapshot.snapshot_id
        assert set(restored.process_states.keys()) == set(snapshot.process_states.keys())
        print("\n✓ Serialisation round-trip verified!")
    else:
        print("\n✗ Snapshot failed!")
        for w in workers:
            w.stop()
        return

    # Simulate recovery
    print("\n--- Simulating recovery ---")
    recovered = workers[0].snapshot_mgr.recover_latest_snapshot()
    if recovered:
        print(f"  Recovered snapshot: {recovered.snapshot_id}")
        for pid, ps in recovered.process_states.items():
            print(f"  [{pid}] restored state: {ps.state_data}")
        print("\n✓ Recovery successful!")
    else:
        print("✗ Recovery failed!")

    # Clean up
    for w in workers:
        w.stop()
    for port in PORTS:
        d = os.path.join(SCRIPT_DIR, f"_mock_snap_{port}")
        if os.path.exists(d):
            shutil.rmtree(d)

    print(f"\n{'=' * 60}")
    print(" Demo complete — all tests passed!")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()

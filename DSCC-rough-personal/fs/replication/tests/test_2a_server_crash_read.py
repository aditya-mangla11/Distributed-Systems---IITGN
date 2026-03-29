#!/usr/bin/env python3
"""
Test 2a – Server crash during read (single-server setup).

Flow:
  1. Start one server on localhost:50060
  2. Client creates and writes a large file (~5 MB)
  3. Kill the server
  4. Client attempts open() for read → hits UNAVAILABLE
  5. A background thread restarts the server after 3 seconds
  6. Client's retry loop reconnects to the SAME server and completes the read
"""

import os
import sys
import time
import signal
import shutil
import subprocess
import threading

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
REPL_DIR    = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
FS_DIR      = os.path.abspath(os.path.join(REPL_DIR, ".."))
SERVER_PY   = os.path.join(REPL_DIR, "server.py")
VENV_PYTHON = os.path.join(REPL_DIR, "venv", "bin", "python")

# Use venv python if available, else system python
PYTHON = VENV_PYTHON if os.path.exists(VENV_PYTHON) else shutil.which("python3") or sys.executable

sys.path.insert(0, FS_DIR)
sys.path.insert(0, REPL_DIR)

from client_stub import ReplicatedAFSClientStub

PORT     = 50060
ADDR     = f"localhost:{PORT}"
DATA_DIR = os.path.join(REPL_DIR, f"test_data_{PORT}")
LOG_FILE = os.path.join(REPL_DIR, f"server_{PORT}.log")
CACHE_DIR = os.path.join("/tmp", "afs_test_2a_cache")

PASS = "\033[92m[ PASS ]\033[0m"
FAIL = "\033[91m[ FAIL ]\033[0m"
SEP  = "=" * 60


# ── Helpers ──────────────────────────────────────────────────

def kill_stale(port):
    """Kill anything already on our port."""
    try:
        result = subprocess.run(["lsof", "-t", f"-i:{port}"],
                                capture_output=True, text=True)
        for pid in result.stdout.strip().split():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass
    except FileNotFoundError:
        pass
    time.sleep(0.5)


def start_server():
    """Start the server subprocess and return Popen handle."""
    log = open(LOG_FILE, "w")
    proc = subprocess.Popen(
        [PYTHON, "-u", SERVER_PY,
         "--addr",     ADDR,
         "--peers",    "",
         "--data-dir", DATA_DIR],
        stdout=log, stderr=log,
        preexec_fn=os.setsid,
    )
    return proc


def wait_server_ready(timeout=15):
    """Block until the server responds to GetClusterInfo."""
    import grpc
    import replication_pb2, replication_pb2_grpc

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ch = grpc.insecure_channel(ADDR)
            stub = replication_pb2_grpc.ReplicationServiceStub(ch)
            resp = stub.GetClusterInfo(
                replication_pb2.GetClusterInfoRequest(), timeout=2.0)
            ch.close()
            if resp.success:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def kill_server(proc):
    """Kill the server process group."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()


def cleanup():
    kill_stale(PORT)
    for d in [DATA_DIR, CACHE_DIR]:
        if os.path.isdir(d):
            shutil.rmtree(d)


# ── Main test ────────────────────────────────────────────────

def main():
    print(SEP)
    print("  Test 2a – Server crash during read (single server)")
    print(SEP)

    cleanup()

    # ── Phase 1: Start server and write a large file ─────────
    print("\n[Phase 1] Starting server and creating a large file …")
    proc = start_server()
    if not wait_server_ready():
        print(f"{FAIL} Server did not start. Check {LOG_FILE}")
        kill_server(proc)
        return

    client = ReplicatedAFSClientStub([ADDR], cache_dir=CACHE_DIR)
    FILE_NAME   = "bigfile.txt"
    FILE_DATA   = b"X" * 2_000_000   # 2 MB (under gRPC 4MB default limit)

    fh = client.create(FILE_NAME)
    fh = client.open(FILE_NAME, "w")
    client.write(fh, 0, FILE_DATA)
    client.close(fh)
    print(f"  Written {len(FILE_DATA)} bytes to {FILE_NAME}")

    # Clear client cache so the next open() must fetch from server
    client.disconnect()
    shutil.rmtree(CACHE_DIR, ignore_errors=True)
    os.makedirs(CACHE_DIR, exist_ok=True)

    # ── Phase 2: Kill the server ─────────────────────────────
    print("\n[Phase 2] Killing the server …")
    kill_server(proc)
    print(f"  Server killed (PID {proc.pid})")

    # ── Phase 3: Restart server in background after a delay ──
    RESTART_DELAY = 3   # seconds
    restarted_proc = [None]

    def restart_after_delay():
        print(f"\n  [BG] Waiting {RESTART_DELAY}s before restarting server …")
        time.sleep(RESTART_DELAY)
        print("  [BG] Restarting server …")
        restarted_proc[0] = start_server()

    bg = threading.Thread(target=restart_after_delay, daemon=True)
    bg.start()

    # ── Phase 4: Client attempts to read (will retry until server is back)
    print("\n[Phase 3] Client attempting to read (server is down, retries expected) …")

    # Recreate client (previous connection is dead)
    client = ReplicatedAFSClientStub([ADDR], cache_dir=CACHE_DIR, max_retries=5)

    try:
        fh   = client.open(FILE_NAME, "r")
        data = client.read(fh, 0, len(FILE_DATA))
        client.close(fh)

        if data == FILE_DATA:
            print(f"\n  {PASS} Read succeeded after server restart! "
                  f"({len(data)} bytes match)")
        else:
            print(f"\n  {FAIL} Data mismatch: got {len(data)} bytes, "
                  f"expected {len(FILE_DATA)}")
    except Exception as e:
        print(f"\n  {FAIL} Read failed: {e}")

    # ── Cleanup ──────────────────────────────────────────────
    client.disconnect()
    bg.join(timeout=10)
    if restarted_proc[0]:
        kill_server(restarted_proc[0])
    cleanup()
    print(f"\n{SEP}")
    print("  Test 2a complete.")
    print(SEP)


if __name__ == "__main__":
    main()

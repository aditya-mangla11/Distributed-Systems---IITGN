# Part 1 — AFS-Like Distributed File System: Architecture & Design Document

> **Course:** Distributed Systems — IIT Gandhinagar  
> **Project:** PrimeScience Distributed Platform  
> **Scope:** Part 1 — Tasks 1A, 1B, 2, and 3

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [High-Level Architecture](#2-high-level-architecture)
3. [AFS Protocol Design (Tasks 1A & 1B)](#3-afs-protocol-design-tasks-1a--1b)
4. [Client-Side Caching (Task 1B)](#4-client-side-caching-task-1b)
5. [Fault Tolerance (Task 2)](#5-fault-tolerance-task-2)
6. [Replication — Two Implementations (Task 3)](#6-replication--two-implementations-task-3)
7. [gRPC & Protobuf Specifications](#7-grpc--protobuf-specifications)
8. [File & Directory Layout](#8-file--directory-layout)
9. [Key Design Decisions Summary](#9-key-design-decisions-summary)
10. [Test Coverage](#10-test-coverage)
11. [How to Run](#11-how-to-run)

---

## 1. System Overview

The system is a **user-space distributed file system** inspired by **AFS v1 (Andrew File System)**. It stores large input datasets (`input_dataset_*.txt`) and result files (`primes.txt`) for a distributed prime-number-finding application.

### Why AFS-Like?

```
┌──────────────────────────────────────────────────────────────────┐
│  Workload Properties              Design Implications            │
│  ─────────────────────            ────────────────────           │
│  • Input files are static    →    No concurrent write conflicts  │
│  • Output is write-once      →    Simple consistency model       │
│  • Read-heavy access         →    Whole-file caching is ideal    │
│  • No kernel-space needed    →    User-space RPC-based approach  │
└──────────────────────────────────────────────────────────────────┘
```

### Why User-Space?

| Concern | Kernel FS (e.g., FUSE) | Our User-Space Approach |
|---|---|---|
| Complexity | High — kernel module or FUSE driver | Low — standard Python + gRPC |
| Debugging | Difficult (kernel panics) | Standard debugger, print statements |
| Portability | OS-specific | Any UNIX with Python 3 |
| Performance | Slightly better (VFS cache) | Acceptable — whole-file caching compensates |

---

## 2. High-Level Architecture

```
                        ┌─────────────────────────────────┐
                        │          CLIENT PROCESS          │
                        │                                  │
                        │  ┌───────────────────────────┐   │
                        │  │   Application Layer        │   │
                        │  │   (Prime Number Finder)    │   │
                        │  └─────────┬─────────────────┘   │
                        │            │ open/read/write/close│
                        │  ┌─────────▼─────────────────┐   │
                        │  │   AFS Client Stub          │   │
                        │  │  • Whole-file caching      │   │
                        │  │  • Version checking        │   │
                        │  │  • Leader/primary redirect  │   │
                        │  │  • Retry + backoff          │   │
                        │  │  • Idempotency (seq_num)    │   │
                        │  └─────────┬─────────────────┘   │
                        │            │ gRPC (fs.proto)      │
                        └────────────┼─────────────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
                    ▼                ▼                ▼
         ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
         │   Server 0   │  │   Server 1   │  │   Server 2   │
         │  (LEADER /   │◄►│  (FOLLOWER / │◄►│  (FOLLOWER / │
         │   PRIMARY)   │  │   BACKUP)    │  │   BACKUP)    │
         │              │  │              │  │              │
         │  ┌────────┐  │  │  ┌────────┐  │  │  ┌────────┐  │
         │  │data_dir│  │  │  │data_dir│  │  │  │data_dir│  │
         │  │ files  │  │  │  │ files  │  │  │  │ files  │  │
         │  └────────┘  │  │  └────────┘  │  │  └────────┘  │
         └──────────────┘  └──────────────┘  └──────────────┘
              :50051            :50052            :50053
```

---

## 3. AFS Protocol Design (Tasks 1A & 1B)

### 3.1 Core Design: AFSv1 Semantics

Our file system follows **AFSv1 whole-file transfer** semantics:

```
  CLIENT                                        SERVER
    │                                              │
    │─────── Open(filename, mode) ────────────────►│
    │◄────── file_data + server_version ───────────│   (whole file downloaded)
    │                                              │
    │  ┌──────────────────────────┐                │
    │  │  LOCAL READS & WRITES    │                │
    │  │  (POSIX file I/O on      │                │
    │  │   cached copy in         │                │
    │  │   /tmp/afs/*)            │                │
    │  └──────────────────────────┘                │
    │                                              │
    │─────── Close(handle, file_data) ────────────►│   (flush if modified)
    │◄────── new_version ──────────────────────────│
    │                                              │
```

### 3.2 RPC Interface (fs.proto)

| RPC | Purpose | Parameters | Returns |
|---|---|---|---|
| `Create` | Create a new empty file on server | `filename`, `client_id`, `seq_num` | `file_handle`, `success` |
| `Open` | Open file; optionally fetch contents | `filename`, `mode` (r/w), `fetch_data` | `file_handle`, `file_data`, `server_version` |
| `Close` | Close file; flush data if modified | `file_handle`, `file_data`, `client_id`, `seq_num` | `success`, `new_version` |
| `TestVersionNumber` | Check server's current file version | `filename` | `version` |

### 3.3 Key Design Choice: No Separate `Read`/`Write` RPCs

```
  ┌─────────────────────────────────────────────────────────────┐
  │  DECISION: Read and Write are LOCAL operations, not RPCs.   │
  │                                                             │
  │  • Open() downloads the whole file to local cache           │
  │  • read() and write() operate on the local cached file      │
  │  • Close() uploads the whole file back (if modified)        │
  │                                                             │
  │  This minimises network round-trips for the read-heavy      │
  │  prime-checking workload. A single file may be read         │
  │  millions of times (one read per number) but only opened    │
  │  and closed once.                                           │
  └─────────────────────────────────────────────────────────────┘
```

### 3.4 File Handle Management

```
  Server maintains:
  ┌──────────────────────────────────────────┐
  │  open_files: dict                        │
  │    handle (int) → {filename, mode}       │
  │                                          │
  │  next_handle: int  (monotonically ↑)     │
  │                                          │
  │  file_versions: dict                     │
  │    filename → version (int, starts at 1) │
  └──────────────────────────────────────────┘

  Version increments on every write-close:
    v1 → v2 → v3 → ...
```

---

## 4. Client-Side Caching (Task 1B)

### 4.1 Cache Flow

```
                     client.open(filename)
                            │
                            ▼
                ┌───────────────────────┐
                │ Is filename in local  │
                │ cache_metadata?       │
                └───────┬───────┬───────┘
                   NO   │       │  YES
                        │       ▼
                        │  ┌─────────────────────────┐
                        │  │ TestVersionNumber(fname) │
                        │  │ Compare local_ver vs     │
                        │  │ server_ver               │
                        │  └──────┬──────────┬────────┘
                        │   MATCH │          │ MISMATCH
                        │         ▼          │
                        │    ┌─────────┐     │
                        │    │CACHE HIT│     │
                        │    │Use local│     │
                        │    │  copy   │     │
                        │    └─────────┘     │
                        │                    │
                        ▼                    ▼
                  ┌──────────────────────────────┐
                  │  CACHE MISS / FIRST ACCESS   │
                  │  Open(fetch_data=True)        │
                  │  Download whole file          │
                  │  Save to: cache_dir/          │
                  │    filename_vN.bin            │
                  │  Delete old versions          │
                  └──────────────────────────────┘
```

### 4.2 Cache Storage Layout

```
  cache_dir/  (e.g., /tmp/afs/ or ./temp_cache_files/)
  ├── input_dataset_001.txt_v1.bin
  ├── input_dataset_001.txt_v2.bin   ← old version auto-deleted
  ├── input_dataset_002.txt_v1.bin
  └── primes.txt_v3.bin
```

### 4.3 Cache Metadata (In-Memory)

```python
cache_metadata = {
    "input_dataset_001.txt": {"version": 2, "path": "./cache/input_dataset_001.txt_v2.bin"},
    "primes.txt":            {"version": 3, "path": "./cache/primes.txt_v3.bin"},
}
```

---

## 5. Fault Tolerance (Task 2)

### 5.1 Fault Model

| Fault Type | Assumed? | Handling Mechanism |
|---|---|---|
| Server crash (fail-stop) | ✅ Yes | Replication + leader redirect |
| Client crash | ✅ Yes | Server-side handle cleanup; no locks held |
| Network partition | ✅ Yes | Timeout + retry + re-discovery |
| Message loss | ✅ Yes | gRPC transport + application retries |
| Message duplication | ✅ Yes | Idempotency via `(client_id, seq_num)` |
| Byzantine faults | ❌ No | Not in scope |

### 5.2 Idempotency Protocol

```
  ┌──────────────────────────────────────────────────────────────────┐
  │                    IDEMPOTENCY MECHANISM                         │
  │                                                                  │
  │  Client assigns:                                                 │
  │    client_id = UUID (unique per client session)                  │
  │    seq_num   = monotonically increasing counter                  │
  │                                                                  │
  │  Server maintains:                                               │
  │    dedup_cache = { client_id → (last_seq_num, cached_response) } │
  │                                                                  │
  │  On every mutating RPC (Create, Close-with-write):               │
  │    1. Check dedup_cache[client_id]                               │
  │    2. If seq_num ≤ last_seq_num → return cached_response         │
  │    3. Else → execute, cache response, return                     │
  └──────────────────────────────────────────────────────────────────┘
```

**Why this matters:**
```
  Client ──── Create("file.txt", seq=5) ────► Server (executes, caches)
       ◄──── ACK lost in network ────────
  Client ──── Create("file.txt", seq=5) ────► Server (dedup: return cached)
       ◄──── cached response ────────────
  
  Result: File created exactly once ✓
```

### 5.3 Client-Side Retry Logic

```
  _execute_rpc(method, request):
  ┌──────────────────────────────────────────────┐
  │  for attempt in range(max_retries + 1):      │
  │    │                                         │
  │    ├─ Call RPC on current server              │
  │    │  │                                      │
  │    │  ├─ SUCCESS → return response           │
  │    │  │                                      │
  │    │  ├─ NOT_LEADER / NOT_PRIMARY             │
  │    │  │  → Parse leader address from error    │
  │    │  │  → Redirect to leader, retry          │
  │    │  │                                      │
  │    │  ├─ UNAVAILABLE / DEADLINE_EXCEEDED      │
  │    │  │  → Round-robin to next node           │
  │    │  │  → sleep(2^attempt) backoff           │
  │    │  │  → retry                             │
  │    │  │                                      │
  │    │  └─ Other error → raise exception        │
  │    │                                         │
  │  raise "Failed after max_retries"             │
  └──────────────────────────────────────────────┘
```

---

## 6. Replication — Two Implementations (Task 3)

We built **two complete replication strategies** for Task 3. Both provide a 3-node highly available cluster.

### 6.1 Comparison Table

| Aspect | Primary-Backup (`replication/`) | Raft Consensus (`raft_*.py`) |
|---|---|---|
| **Algorithm** | Custom primary-backup with heartbeat election | Full Raft (§5 of Ongaro & Ousterhout) |
| **Consistency** | Eventual (sync-before-ACK for writes) | Strong — log committed by majority quorum |
| **Leader Election** | Live RPC queries → lowest-address alive wins | Randomised timeouts + RequestVote quorum |
| **Log Replication** | Direct file replication (whole-file push) | Raft replicated log → state machine apply |
| **Persistence** | Files on disk, versions in memory | Hard state (term, votedFor) + log on disk (JSON) |
| **Recovery** | `SyncState` RPC (full snapshot transfer) | Raft log replay + `InstallSnapshot` |
| **Complexity** | Lower — suited for this workload | Higher — general-purpose consensus |
| **Split-brain safety** | Mitigated (live RPC election) | Guaranteed (quorum-based) |
| **Test cases** | 32 automated tests | 5 tests (create, cache, consistency, versions, failover) |

### 6.2 Primary-Backup Architecture (`replication/`)

```
  ┌─────────────────────────────────────────────────────────────────────┐
  │                     PRIMARY-BACKUP REPLICATION                      │
  │                                                                     │
  │   Client                Primary              Backup 1   Backup 2   │
  │     │                     │                     │          │        │
  │     │── Close(write) ────►│                     │          │        │
  │     │                     │── ReplicateWrite ──►│          │        │
  │     │                     │── ReplicateWrite ────────────►│        │
  │     │                     │◄── ACK ─────────────│          │        │
  │     │                     │◄── ACK ────────────────────────│        │
  │     │◄── ACK ─────────────│                     │          │        │
  │     │                     │                     │          │        │
  │   Write is replicated BEFORE client gets acknowledgement           │
  └─────────────────────────────────────────────────────────────────────┘
```

#### Primary Election Flow

```
  Server Startup
       │
       ▼
  _determine_startup_role()
       │
       ├── Query all peers: GetClusterInfo()
       │     │
       │     ├── Peer says: "Primary is X"
       │     │     │
       │     │     ├── X == me  → I am PRIMARY (confirmed)
       │     │     └── X != me  → SYNC from X, become BACKUP
       │     │
       │     └── No peers respond
       │           │
       │           ├── I have lowest address → become PRIMARY
       │           └── I don't → wait for preferred primary, sync
       │
       ▼
  _sync_or_elect(target)
       │
       ├── Try SyncState(target) for 15s
       │     ├── Success → BACKUP, ready
       │     └── Failure → _elect_new_primary()
       │                    │
       │                    ├── Query all peers (live RPCs)
       │                    ├── alive = [self + responding peers]
       │                    └── new_primary = sorted(alive)[0]
       │                         │
       │                         └── AnnounceLeader() to all peers
       ▼
  Start heartbeat_sender + failure_detector threads
```

#### Heartbeat & Failure Detection Timeline

```
  Time    Server 0 (Primary)    Server 1 (Backup)     Server 2 (Backup)
  ─────   ──────────────────    ─────────────────     ─────────────────
  t=0     Starts                last_hb[S0] = 0       last_hb[S0] = 0
  t=2     Heartbeat ──────────► last_hb[S0] = 2.0     last_hb[S0] = 2.0
  t=4     Heartbeat ──────────► last_hb[S0] = 4.0     last_hb[S0] = 4.0
  t=6     Heartbeat ──────────► last_hb[S0] = 6.0     last_hb[S0] = 6.0
  t=8     Heartbeat ──────────► last_hb[S0] = 8.0     last_hb[S0] = 8.0
  t=10    💥 CRASHES
  t=12    (dead)                now-8 = 4s < 6s ✗     now-8 = 4s < 6s ✗
  t=14    (dead)                now-8 = 6s ≥ 6s ✓     now-8 = 6s ≥ 6s ✓
                                ELECTION TRIGGERED!     ELECTION TRIGGERED!
  t=15                          Queries peers...        Queries peers...
  t=16                          New primary = S1        Receives AnnounceLeader
                                (lowest alive addr)     Updates primary_addr
```

| Constant | Value | Purpose |
|---|---|---|
| `HEARTBEAT_INTERVAL` | 2 seconds | Frequency of heartbeat sends |
| `HEARTBEAT_TIMEOUT` | 6 seconds | Time without heartbeat before declaring failure |
| `last_heartbeat` init | `0.0` | Prevents false elections on fresh startup |

#### Recovery Flow

```
  Server 0 (was Primary, crashed) restarts:
       │
       ▼
  _determine_startup_role()
       │
       ├── Query peers: "Who is primary?"
       │   Peers respond: "Server 1 is primary"
       │
       ├── Server 0 ≠ Server 1, so:
       │   SyncState(Server 1) ──► Gets all files + versions
       │   Writes files to disk
       │   Sets role = BACKUP
       │   Sets ready = True
       │
       └── No split-brain! ✓
```

### 6.3 Raft Consensus Architecture (`raft_*.py`)

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │                        RAFT CONSENSUS FLOW                           │
  │                                                                      │
  │  Client                Leader               Follower 1  Follower 2  │
  │    │                     │                      │           │        │
  │    │── Close(write) ────►│                      │           │        │
  │    │                     │ append to local log   │           │        │
  │    │                     │── AppendEntries ─────►│           │        │
  │    │                     │── AppendEntries ──────────────────►│       │
  │    │                     │◄── success ───────────│           │        │
  │    │                     │    (majority = 2/3)   │           │        │
  │    │                     │                      │           │        │
  │    │                     │ COMMIT (advance       │           │        │
  │    │                     │ commit_index)         │           │        │
  │    │                     │ Apply to state machine│           │        │
  │    │◄── ACK ─────────────│                      │           │        │
  │    │                     │                      │           │        │
  │    │                     │ Next heartbeat:       │           │        │
  │    │                     │── AE(leader_commit) ─►│           │        │
  │    │                     │                      │ Follower  │        │
  │    │                     │                      │ applies   │        │
  └──────────────────────────────────────────────────────────────────────┘
```

#### Raft State Machine

```
                    ┌───────────────────────────────┐
                    │         RAFT NODE STATES       │
                    │                               │
         timeout    │  ┌──────────┐  higher term    │
        ┌──────────►│  │ FOLLOWER │◄────────────┐   │
        │           │  └────┬─────┘             │   │
        │           │       │ election          │   │
        │           │       │ timeout           │   │
        │           │       ▼                   │   │
        │           │  ┌───────────┐            │   │
        │           │  │ CANDIDATE │────────────┘   │
        │           │  └─────┬─────┘  lost/         │
        │           │        │        higher term   │
        │           │        │ won                  │
        │           │        │ (majority votes)     │
        │           │        ▼                      │
        │           │  ┌──────────┐                 │
        └───────────│  │  LEADER  │                 │
         higher     │  └──────────┘                 │
         term       └───────────────────────────────┘
```

#### Raft Log Entry Structure

```python
LogEntry = {
    "term":      int,      # Raft term when entry was created
    "index":     int,      # Position in the log (1-indexed)
    "op_type":   str,      # "create" | "write" | "noop"
    "filename":  str,      # Target file
    "data":      str,      # Hex-encoded file content
    "client_id": str,      # For idempotency
    "seq_num":   int,      # For idempotency
}
```

#### Raft Persistence

```
  raft_state_nodeN/
  ├── nodeN_hard_state.json    ← { current_term, voted_for }
  └── nodeN_log.json           ← [ {term, index, op_type, ...}, ... ]

  Written atomically:
    1. Write to .tmp file
    2. os.replace(tmp, target)  ← atomic rename (POSIX guarantee)
```

#### Raft Timing Constants

| Constant | Value | Purpose |
|---|---|---|
| `ELECTION_TIMEOUT_MIN` | 2.0s | Minimum random election timeout |
| `ELECTION_TIMEOUT_MAX` | 4.0s | Maximum random election timeout |
| `HEARTBEAT_INTERVAL` | 0.5s | Leader sends AppendEntries this often |

#### Raft Key Safety Properties

| Property | How It's Ensured |
|---|---|
| **Election Safety** | At most one leader per term (each node votes once per term) |
| **Leader Append-Only** | Leader never overwrites/deletes its own log entries |
| **Log Matching** | AppendEntries consistency check (`prevLogIndex`, `prevLogTerm`) |
| **Leader Completeness** | Voters reject candidates with shorter/older logs |
| **State Machine Safety** | Only committed entries are applied; committed = majority replicated |

#### Raft vs Primary-Backup: Flow Comparison

```
  PRIMARY-BACKUP:                    RAFT:
  
  Client → Primary                   Client → Leader
  Primary writes file to disk        Leader appends to log
  Primary → ReplicateWrite → Backups Leader → AppendEntries → Followers
  Backups write to disk              Followers append to log
  Backups → ACK                      Followers → ACK
  Primary → ACK → Client            Leader: majority? → commit
                                     Leader applies to state machine
                                     Leader → ACK → Client
                                     Next heartbeat → followers commit
```

---

## 7. gRPC & Protobuf Specifications

### 7.1 Client-Facing Service (`fs.proto`)

```protobuf
service AFSFileSystem {
  rpc Create            (CreateRequest)       returns (CreateResponse)       {}
  rpc Open              (OpenRequest)         returns (OpenResponse)         {}
  rpc Close             (CloseRequest)        returns (CloseResponse)        {}
  rpc TestVersionNumber (TestVersionRequest)  returns (TestVersionResponse)  {}
}
```

### 7.2 Raft Inter-Node Service (`raft.proto`)

```protobuf
service RaftService {
  rpc RequestVote     (RequestVoteArgs)      returns (RequestVoteReply)      {}
  rpc AppendEntries   (AppendEntriesArgs)    returns (AppendEntriesReply)    {}
  rpc InstallSnapshot (InstallSnapshotArgs)  returns (InstallSnapshotReply)  {}
  rpc GetLeader       (GetLeaderRequest)     returns (GetLeaderReply)        {}
}
```

### 7.3 Primary-Backup Replication Service (`replication.proto`)

```protobuf
service ReplicationService {
  rpc ReplicateWrite   (ReplicateWriteRequest)   returns (ReplicateWriteResponse)   {}
  rpc ReplicateCreate  (ReplicateCreateRequest)  returns (ReplicateCreateResponse)  {}
  rpc Heartbeat        (HeartbeatRequest)        returns (HeartbeatResponse)        {}
  rpc GetClusterInfo   (GetClusterInfoRequest)   returns (GetClusterInfoResponse)   {}
  rpc SyncState        (SyncStateRequest)        returns (SyncStateResponse)        {}
  rpc AnnounceLeader   (AnnounceLeaderRequest)   returns (AnnounceLeaderResponse)   {}
}
```

### 7.4 Message Summary Table

| Message | Fields | Used By |
|---|---|---|
| `CreateRequest` | filename, client_id, seq_num | Client → Server |
| `OpenRequest` | filename, mode, fetch_data | Client → Server |
| `CloseRequest` | file_handle, file_data, client_id, seq_num | Client → Server |
| `TestVersionRequest` | filename | Client → Server |
| `RequestVoteArgs` | term, candidate_id, last_log_index, last_log_term | Raft node → node |
| `AppendEntriesArgs` | term, leader_id, prev_log_index, prev_log_term, entries[], leader_commit | Leader → Followers |
| `InstallSnapshotArgs` | term, leader_id, last_included_index/term, snapshot_data | Leader → Follower |
| `ReplicateWriteRequest` | filename, file_data, new_version | Primary → Backups |
| `SyncStateResponse` | FileEntry[] (filename, data, version) | Primary → Recovering server |

---

## 8. File & Directory Layout

### 8.1 Submission Structure

```
submission/
├── raft.proto                  # Raft inter-node RPC definitions
├── raft_node.py                # Core Raft consensus engine (566 lines)
│                               #   Leader election, log replication,
│                               #   persistence, commit advancement
├── raft_server.py              # gRPC server: hosts both AFSFileSystem
│                               #   and RaftService on same port
├── raft_client_stub.py         # Client stub with Raft-aware leader redirect
├── raft_client_test.py         # 5 functional tests for Raft cluster
├── nodes_config.json           # 3-node cluster configuration
├── setup_raft.sh               # One-time dependency install + protoc
├── start_cluster.sh            # Launch 3 Raft nodes locally
├── stop_cluster.sh             # Kill all cluster nodes
│
└── replication/                # PRIMARY-BACKUP implementation
    ├── replication.proto       # Peer-to-peer RPC definitions
    ├── server.py               # Unified primary/backup server (580 lines)
    │                           #   Both AFSFileSystem + ReplicationService
    ├── client_stub.py          # Client stub with primary discovery
    ├── client.py               # Interactive test client
    ├── generate_proto.sh       # Regenerate protobuf stubs
    ├── start_cluster.sh        # Launch 3-node cluster locally
    ├── replication.md          # Detailed design document
    └── tests/
        ├── test_suite.py       # 32 automated test cases
        └── run_local.py        # Automated: start cluster → test → teardown
```

### 8.2 Runtime Directories

```
  Per-node data (Raft):          Per-node data (Primary-Backup):
  ├── server_data_node0/         ├── data_50050/
  │   ├── input_dataset_001.txt  │   ├── input_dataset_001.txt
  │   └── primes.txt             │   └── primes.txt
  ├── server_data_node1/         ├── data_50051/
  │   └── ...                    │   └── ...
  ├── server_data_node2/         └── data_50052/
  │   └── ...                        └── ...
  ├── raft_state_node0/
  │   ├── node0_hard_state.json
  │   └── node0_log.json
  ├── raft_state_node1/
  └── raft_state_node2/

  Client cache:
  └── temp_cache_files/
      ├── input_dataset_001.txt_v1.bin
      └── primes.txt_v2.bin
```

---

## 9. Key Design Decisions Summary

| # | Decision | Alternatives Considered | Rationale |
|---|---|---|---|
| 1 | **AFSv1 whole-file semantics** | NFSv3-style block transfers | Read-heavy workload; fewer round-trips; simpler caching |
| 2 | **User-space implementation** | FUSE / kernel module | Portability; easier debugging; no root access needed |
| 3 | **gRPC + Protobuf for RPC** | Raw TCP sockets; REST/HTTP | Strongly typed; streaming support; code generation; standard |
| 4 | **Python** | C, Go | Rapid prototyping; gRPC-tools available; sufficient performance |
| 5 | **Version-number-based cache invalidation** | Timestamp; callback-based (AFSv2) | Simple, deterministic; no clock sync needed |
| 6 | **`(client_id, seq_num)` idempotency** | At-most-once with timeouts | Handles network retries; prevents duplicate creates/writes |
| 7 | **Two replication strategies** | Just one | Primary-backup for simplicity; Raft for correctness guarantees |
| 8 | **Primary-backup: sync-before-ACK** | Async replication | Prevents data loss if primary crashes right after ACK |
| 9 | **Primary-backup: live RPC election** | Heartbeat-timestamp election | Avoids stale-timestamp split-brain; accurate liveness check |
| 10 | **Primary-backup: lowest-address-wins** | Raft-style random timeout | Deterministic; no extra voting rounds; simple |
| 11 | **Raft: no-op on leader election** | Direct commit of old-term entries | Ensures entries from previous terms are committed (§5.4.2) |
| 12 | **Raft: fast conflict resolution** | Decrement nextIndex by 1 | Sends `conflict_index` + `conflict_term` for O(term) catchup |
| 13 | **Raft: JSON persistence** | SQLite; binary format | Human-readable for debugging; sufficient for this scale |
| 14 | **Raft: atomic writes (tmp + rename)** | Direct overwrite | Crash-safe persistence; no partial writes |
| 15 | **Both services on same gRPC port** | Separate ports | Simpler configuration; single connection per node |
| 16 | **Read-only ops served by any replica** | All ops go through leader | Reduces leader load; acceptable for this consistency model |
| 17 | **gRPC short reconnect backoff** | Default gRPC backoff (seconds) | Faster replication recovery after peer restart |

---

## 10. Test Coverage

### 10.1 Primary-Backup Test Suite (32 Tests)

| Group | Tests | What's Verified |
|---|---|---|
| **A — Basic AFS** | T01–T08 | create, open, read, write, close, versions, large files, empty content |
| **B — Caching** | T09–T11 | Cache hit, cache miss after remote write, repeated opens |
| **C — Replication** | T12–T14 | All 3 nodes have data, same version, direct backup read |
| **D — Primary Failover** | T15–T19 | Election, reads/writes after failover, lowest-addr wins, sequential writes |
| **E — Backup Failure** | T20–T21 | Primary continues when backup is down |
| **F — Recovery** | T22–T24 | Restarted backup syncs, old primary syncs, old primary stays backup |
| **G — Idempotency** | T25–T26 | Duplicate create returns cached, duplicate close doesn't double-write |
| **H — Concurrency** | T27–T28 | Two writers to different files, four concurrent readers |
| **I — Edge Cases** | T29–T32 | Read past EOF, non-zero offset read, invalid handle, non-existent file version |

### 10.2 Raft Test Suite (5 Tests)

| Test | What's Verified |
|---|---|
| Basic create/write/read | End-to-end file operations through Raft |
| Cache hit on re-open | Client caching works with Raft backend |
| Read consistency across nodes | All 3 nodes return same data |
| Version increments | Multiple writes increment versions correctly |
| Leader failover | Operations succeed after killing leader |

---

## 11. How to Run

### 11.1 Raft Cluster

```bash
# One-time setup
cd submission/
chmod +x setup_raft.sh start_cluster.sh stop_cluster.sh
./setup_raft.sh

# Seed input files
cp input.txt server_data_node0/
cp input.txt server_data_node1/
cp input.txt server_data_node2/

# Start cluster (3 nodes on ports 50051-50053)
./start_cluster.sh
# Wait ~4s for leader election, check: tail -f logs/node0.log

# Run tests
python raft_client_test.py

# Stop cluster
./stop_cluster.sh
```

### 11.2 Primary-Backup Cluster

```bash
cd submission/replication/
python3 -m venv venv
./venv/bin/pip install grpcio grpcio-tools
bash generate_proto.sh

# Start cluster (3 servers on ports 50050-50052)
bash start_cluster.sh

# Run full test suite (32 tests)
./venv/bin/python tests/run_local.py

# Smoke test (no fault injection)
./venv/bin/python tests/run_local.py --no-fault
```

### 11.3 Cluster Configuration (Raft)

```json
{
  "nodes": [
    { "id": 0, "host": "localhost", "port": 50051,
      "data_dir": "./server_data_node0", "raft_dir": "./raft_state_node0" },
    { "id": 1, "host": "localhost", "port": 50052,
      "data_dir": "./server_data_node1", "raft_dir": "./raft_state_node1" },
    { "id": 2, "host": "localhost", "port": 50053,
      "data_dir": "./server_data_node2", "raft_dir": "./raft_state_node2" }
  ]
}
```

---

*Document generated for the Part 1 submission of the Distributed Systems final project.*

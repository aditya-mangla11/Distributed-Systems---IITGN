# FINAL-PROJECT

**Due:** 29 Mar by 23:59 | **Points:** 0 | **Submitting:** Text entry box  
**Available:** 1 Mar at 0:00 – 30 Mar at 23:59

---

## Introduction

You recently graduated from IIT Gandhinagar and have started working at **PrimeScience**, a cutting-edge startup in the cryptography and blockchain space. The company has been focusing on developing mathematical libraries involving prime numbers, which are crucial for RSA encryption and cryptocurrency mining algorithms. Your company's current single-server solution can handle small datasets. Your company has just received a massive contract with a major tech company that requires processing petabytes of numerical data to discover large prime numbers for next-generation encryption systems.

Your CEO, who notoriously avoided the Distributed Systems course, suddenly realizes the company needs to scale beyond a single machine. The CEO recalls that you were bragging about the Distributed Systems course during the interview. Your CEO pulls you into a conference room and says, *"We have 4 weeks to deliver a distributed platform that can provide large prime numbers by processing data 1000x larger than what we currently handle. The client is paying us millions, and our competitors are breathing down our necks. Can you make this happen?"*

You take a deep breath, sit down at your desk, and start thinking about the project. You want to make the CEO regret not taking a Distributed Systems class in the past. Of course, you may understand the regret of taking the Distributed Systems class.

After analyzing the requirements, you recall how your course instructor taught you to start visualizing the system abstractly initially. You realize the project perfectly maps to classic distributed systems problems. The solution needs two crucial components:

1. A **distributed filesystem**, where the dataset is stored and accessed in a file server.
2. A **distributed prime number finding application** that will access the distributed filesystem to access the dataset.

You sit down and start capturing your thoughts around these two components.

---

## Part 1: A User-Space AFS-Like Distributed File System

The massive input datasets (multiple files, containing billions of numbers, in random order, and may have duplicates) need to be stored and accessed reliably across machines. You decide to implement an **Andrew File System (AFS)-inspired** user-space distributed file system.

**Why AFS-like?** Since prime number identification is read-heavy (workers only read input files and write final results on finding a prime number(s) in those datasets), you can simplify the design:

- **No concurrent writes:** Input files are static; output files are write-once
- **Whole-file caching:** Clients can cache entire files locally
- **Simple consistency:** No need for complex cache invalidation

**Why user-space?** Your file system workload is mostly read-only with occasional writes. You want to avoid mounting the file system, writing complex code in the Linux kernel, etc.

The filesystem will store:

- **Large input files:** `input_dataset_001.txt`, `input_dataset_002.txt`, etc., each containing millions of numbers
- **Result files:** `primes.txt`, one single output file containing discovered prime numbers from `input_dataset_*.txt` files

You recall that you need to worry about **fault-tolerance and availability** in a distributed system.

---

## Part 2: Fault-Tolerant Distributed Prime Number Finding Application

The prime number finding application will analyze the numbers present in the massive input files stored in the distributed file system and identify all **unique prime numbers** within those input files. The final output should never have duplicates. Duplicate prime numbers will break the protocol, leading to incorrect results. Please note that duplicates may exist across different files; each file may be processed separately.

The beauty of primality testing is that it is **embarrassingly parallel** — each number can be independently tested, making it perfect for distributed processing across multiple machines. You are free to choose any primality algorithm you want.

The application takes **periodic snapshots** to recover from faults and avoid redoing the work post-failure recovery. The application recovers from failures by restoring to the latest snapshot. *(Think about how the application, snapshots, and the distributed filesystem will work in tandem to restore from a particular snapshot.)*

---

## Environment

The coursework should be done on a **UNIX-based platform** (Linux preferred).

Students may run their system using:

- A local Linux installation
- A Linux virtual machine (VirtualBox/VMware/UTM)
- Windows Subsystem for Linux (WSL)
- Containers (Docker/Podman)

Your clients and servers can be represented as processes, containers, or virtual machines.

**Environment setup guides:**

- [Ubuntu Linux download](https://ubuntu.com/download/desktop)
- [Install Ubuntu](https://ubuntu.com/tutorials/install-ubuntu-desktop)
- [VirtualBox VM](https://www.virtualbox.org/wiki/Downloads)
- [Ubuntu on VirtualBox](https://ubuntu.com/tutorials/how-to-run-ubuntu-desktop-on-a-virtual-machine-using-virtualbox)
- [WSL install guide](https://learn.microsoft.com/en-us/windows/wsl/install)
- [Docker install](https://docs.docker.com/get-docker/)
- [Podman install](https://podman.io/docs/installation)

---

## Specification

### Part 1: AFS-Like User-Space Distributed File System (45%)

You must implement a highly available user-space distributed file system inspired by the Andrew File System (AFS). The clients will run the distributed application and execute the primality testing algorithm.

The clients will open the remote file. Upon file open, the client will request the entire file from the server and store it in the local drive. Subsequent read and write requests will be redirected to said local copy. When the client issues a close, the file contents are flushed to the server if the file is modified. We will be using a protocol similar to **AFSv1** (to make life a little simpler).

You will create **RPCs** to interact with the distributed file system. For the local read and writes, you will use the standard POSIX APIs. You need to support basic file system functionality — open, create, read, write, close files, and all other relevant functionality that you may need.

The client side will initialize with a local directory pathname where the remote files are cached (e.g., `/tmp/afs`). The local directory pathname should be specified while starting the client.

The server initializes with two directory pathnames; this path is where the files in this file system reside (i.e., the persistent state of the file system is stored). The clients will ask the server for the directory path upon initialization. You should assume all the input files reside in the server directory beforehand. You do not have to create or delete them. The output file is stored in another directory. Only the output file will be created and deleted (in case of a recovery). Please follow the rules to name the input and output files as mentioned above.

Your file system server should be **highly available**. You should rely on replication to ensure high availability. If one server is down, clients can access files from other replicas. You can either use simple state machine replication or implement consensus algorithms such as Paxos or Raft. Once the faulty server recovers, you will need to sync the server to ensure all the replicas have a consistent view. Please think carefully — do not overcomplicate things; stick to a simple approach.

Your file system servers need to handle faults gracefully and recover properly without creating correctness issues.

#### Background Reading

- [File System Intro](https://pages.cs.wisc.edu/~remzi/OSTEP/file-intro.pdf)
- [File System Implementation](https://pages.cs.wisc.edu/~remzi/OSTEP/file-implementation.pdf)
- [AFS](https://pages.cs.wisc.edu/~remzi/OSTEP/dist-afs.pdf)
- [Primality Testing](https://cp-algorithms.com/algebra/primality_tests.html)
- [RPC](https://dl.acm.org/doi/pdf/10.1145/2080.357392)
- [Distributed Snapshot](https://decomposition.al/blog/2019/04/26/an-example-run-of-the-chandy-lamport-snapshot-algorithm/)
- [Replication](https://www.cs.utexas.edu/~lorenzo/corsi/cs380d/papers/statemachines.pdf)

#### Task Breakdown

- **Task 1A: Basic Client-Server with RPC (15%)**  
  Get a single-file server working with remote clients using RPC (Remote Procedure Call). Implement basic RPC versions of `open()`, `create()`, `read()`, `write()`, and `close()`.

- **Task 1B: Client-side File Caching (5%)**  
  Add whole-file caching to clients. Clients will request the entire file from the server and store it locally upon the first file open. Subsequent read/write operations should happen locally, and writes will be flushed to the server when the file is closed. On subsequent opens, the client will send a `TestAuth` message to the file server to determine whether the file has changed. If not, the client uses the existing locally cached copy.

- **Task 2: Support Fault Tolerance (10%)**  
  Depending on your system model, you will need to identify all the faults that may occur and handle them gracefully. *(Hint: Try to take the approach discussed in class for RPC + Fault Tolerance.)*

- **Task 3: Replicate the File Server (15%)**  
  Add multiple (at least three) replica servers to support server failures. One approach is **primary-backup replication**, where one primary server handles all writes and multiple backup servers replicate the data; if the primary fails, the replicas elect a new primary. An alternate approach is to rely on consensus algorithms such as Raft and Paxos.

> **Note:** You are **not allowed** to use any existing libraries to implement Tasks 1, 2, and 3.

---

### Part 2: Distributed Application for Finding Prime Numbers (35%)

The core functionality of your distributed application comprises reading a large input file from the distributed file system, processing the numbers, reporting the unique prime numbers, and saving them in one output file. You need to ensure that the prime numbers are **unique and not repeated** across the output file. You can assume that all numbers in the input files are **64-bit unsigned integers**, with one number per line.

As primality testing is embarrassingly parallel, you can distribute the computation across multiple workers (clients). You have complete freedom to choose the application architecture:

- **Coordinator-worker design:** A centralized coordinator assigns work to multiple workers.
- **Peer-to-peer:** Workers self-organize, share work, and coordinate without a central coordinator.
- **Map-reduce-like solution:** A third alternative approach.

In any case, you will need to show that your application **scales with the number of workers**.

Your application needs to handle worker failures. You will do so by periodically taking **consistent snapshots** using the **Lamport-Chandy algorithm** and storing all snapshot data in the distributed file system. The snapshot comprises the state of the system (workers), the input and output files they are working on, the number of workers in the system, etc. You will implement a recovery mechanism that can fully restore the application from the most recent snapshot.

Without snapshots, any crash would require restarting the entire computation. With such a huge dataset, restarting the computation could waste hours of work. Snapshots allow quick recovery from the latest checkpoint. Your recovery mechanism will differ depending on the number of workers failing — if a single worker fails, only that worker needs to recover; if the majority fail, all workers recover from the snapshot.

#### Task Breakdown

- **Task 4: Basic Distributed Application (20%)**  
  Build an application that accesses the distributed file system to find all unique prime numbers from a large input dataset and store the results.

- **Task 5: Support Fault Tolerance Using Global Snapshots (15%)**  
  The application will periodically capture a consistent global snapshot of its state and save it on the distributed file system. On worker failure(s), the application will recover from the latest snapshot instead of starting over.

> **Note:** You may use any method for primality testing, including existing libraries. You **cannot** use existing libraries for any other purposes in Tasks 4 and 5.

---

## Gentle Reminder

You can work on both components **in parallel**! Since this is a team project, you can split your team to maximize efficiency — 2–3 people can work on the distributed file system while the rest focus on the distributed prime finding application.

The application does **not** need to wait for the distributed file system to be complete. You can use the local file system initially and integrate later when both components are ready.

---

## Submission (for Part 1)

You should submit:

- Source code
- Instructions to run your system
- A short design document explaining your system model and design decisions *(Max 2 pages)*

You will also be required to **present a demo** of your system.

**Due Date, Part 1:** 29/03/2026 11:59 P.M.

**Collaboration:** This is a group assignment. We recommend working as a group of 3 members.

**Programming Language:** C, Python, Go, or Other

**Doubts and Questions:** Ask on the course Canvas page, in labs, or after lectures.

> **Important Note:** No late submissions or extensions will be entertained. Use your slip days wisely. For each of Parts 1 and 2, you are allowed to use at most **one slip day**, provided you have slip days remaining. If you have an emergency, please contact us directly.

> **Design Document:** For Part 1, no design document is required. A 2-page document discussing design details, system model, and assumptions is due with Part 2.

> **Also Important:** For Part 2, you can only utilize the code submitted in Part 1.

---

## Objectives

- **Outcome 1:** Develop an understanding of the principles of distributed systems and be able to demonstrate this by explaining them.
- **Outcome 2:** Give an account of the trade-offs which must be made when designing a distributed system and make such trade-offs in their own designs.
- **Outcome 3:** Develop practical skills in the implementation of distributed algorithms in software, taking an algorithm description and realizing it in software.
- **Outcome 4:** Give an account of the models used to design distributed systems and manipulate those models to reason about such systems.
- **Outcome 5:** Design efficient algorithms for distributed computing tasks.

---

## Grading and Submission

| Component | Weight |
|---|---|
| Distributed File System (Part 1) | 45% |
| Distributed Application (Part 2) | 35% |
| Presentation | 15% |
| Design Report | 5% |

You are not required to complete all tasks. Upon completing Tasks 1 (20%) and 4 (20%), along with the presentation (15%) and design document (5%), you can earn up to **60%** of the points.

During the presentation, you will briefly explain your system, followed by a demo. More information about the presentation will be released towards the end of the deadline.

---

## Success Metrics

A successful coursework will exhibit the following:

- **Scale:** Process datasets much larger than a single machine could handle
- **Survive:** Continue operating through one file server failure
- **Recover:** Restore from snapshots without losing significant work
- **Perform:** Show performance improvement with additional workers
- **Deliver:** Produce correct results consistently and store persistently

Your presentation should focus on the above points, and your demo should convince us that your system works as intended.

---

*The client contract is worth millions and could establish PrimeScience as the leader in distributed mathematical computing. Success means the company goes public next year. Failure means your CEO will definitely not regret not taking that Distributed Systems class. Time to prove that distributed systems knowledge is worth its weight in prime numbers.*

import argparse
import math
import time
from typing import Iterable, List, Set

from client_stub import PrimeFSClientStub


def is_prime(n):
    if n < 2:
        return False
    if n in (2, 3):
        return True
    if n % 2 == 0:
        return False

    limit = int(math.isqrt(n))
    d = 3
    while d <= limit:
        if n % d == 0:
            return False
        d += 2
    return True


def read_all_bytes(client, handle, chunk_size = 4096):
    data_parts: List[bytes] = []
    offset = 0

    while True:
        chunk = client.read(handle, offset, chunk_size)
        if not chunk:
            break
        data_parts.append(chunk)
        offset += len(chunk)

    return b"".join(data_parts)


def parse_numbers(file_bytes):
    numbers: List[int] = []
    text = file_bytes.decode("utf-8")

    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            numbers.append(int(line))
        except ValueError:
            print(f"[WARN] Skipping invalid integer at line {line_no}: {raw!r}")

    return numbers


def append_new_primes_with_lock(client, results_file, candidate_primes, lock_timeout_seconds = 5):
    try:
        handle = client.open(results_file, mode="r")
        client.close(handle)
    except Exception:
        try:
            client.create(results_file)
        except Exception:
            pass

    lock_acquired = False

    def unlock_with_retries():
        for attempt in range(1, 6):
            try:
                client.unlock_file(results_file)
                return True
            except Exception as e:
                if attempt == 5:
                    print(f"[WARN] Failed to unlock {results_file} after retries: {e}")
                    return False
                time.sleep(0.2)

    try:
        client.lock_file(results_file, timeout_seconds=lock_timeout_seconds)
        lock_acquired = True

        read_handle = client.open(results_file, mode="r")
        try:
            latest_raw = read_all_bytes(client, read_handle)
        finally:
            client.close(read_handle)

        existing = set(parse_numbers(latest_raw))
        seen_candidates: Set[int] = set()
        to_append: List[int] = []

        for value in candidate_primes:
            if value in seen_candidates:
                continue
            seen_candidates.add(value)
            if value not in existing:
                to_append.append(value)
                existing.add(value)

        if not to_append:
            return 0

        try:
            write_handle = client.open(results_file, mode="w")
        except Exception as e:
            if lock_acquired and "currently locked for writes" in str(e):
                unlock_with_retries()
                lock_acquired = False
                write_handle = client.open(results_file, mode="w")
            else:
                raise

        try:
            existing_data = read_all_bytes(client, write_handle)
            text = existing_data.decode("utf-8")
            offset = len(existing_data)

            if text and not text.endswith("\n"):
                client.write(write_handle, offset, "\n")
                offset += 1

            count = 0
            for value in to_append:
                line = f"{value}\n"
                client.write(write_handle, offset, line)
                offset += len(line.encode("utf-8"))
                count += 1

            return count
        finally:
            try:
                client.close(write_handle)
            except Exception:
                if lock_acquired:
                    unlock_with_retries()
                    lock_acquired = False
                client.close(write_handle)
    finally:
        if lock_acquired:
            unlock_with_retries()


def process_dataset(client, dataset_file, results_file):
    input_handle = client.open(dataset_file, mode="r")
    try:
        raw = read_all_bytes(client, input_handle)
    finally:
        client.close(input_handle)

    numbers = parse_numbers(raw)
    seen_in_file: Set[int] = set()
    candidate_primes: List[int] = []

    for value in numbers:
        if value in seen_in_file:
            continue
        seen_in_file.add(value)

        if is_prime(value):
            candidate_primes.append(value)

    added = append_new_primes_with_lock(client, results_file, candidate_primes)
    print(f"[INFO] {dataset_file}: added {added} new prime number(s) to {results_file}")
    return added


def main() -> None:
    parser = argparse.ArgumentParser(description="Read input dataset files from PrimeFS and append unique primes into results.txt")
    parser.add_argument("datasets", nargs="+", help="Dataset file(s), e.g. input_dataset_1.txt or input_dataset_*.txt (shell-expanded)")
    parser.add_argument("--results", default="results.txt", help="Results file name in PrimeFS (default: results.txt)")
    args = parser.parse_args()

    client = PrimeFSClientStub()
    total_added = 0

    try:
        for dataset_file in args.datasets:
            total_added += process_dataset(client, dataset_file, args.results)
    finally:
        client.disconnect()

    print(f"[DONE] Total new primes added: {total_added}")


if __name__ == "__main__":
    main()

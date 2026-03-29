import os
from client_stub import SecureFSClientStub


def print_header(text):
    print(f"\n{'='*60}")
    print(f" {text}")
    print(f"{'='*60}")


def run_exhaustive_tests():
    client = SecureFSClientStub()
    run_id = str(os.getpid())

    f_main = f"input_fileno_001_{run_id}.txt"
    f_chunks = f"res_{run_id}.txt"
    f_str = f"str_write_{run_id}.txt"
    f_ver = f"version_{run_id}.txt"
    f_overwrite = f"overwrite_{run_id}.txt"

    passed = 0
    failed = 0

    def ok(msg):
        nonlocal passed
        passed += 1
        print(f"[PASS] {msg}")

    def fail(msg):
        nonlocal failed
        failed += 1
        print(f"[FAIL] {msg}")

    def expect_exception(msg, fn):
        try:
            fn()
            fail(msg)
        except Exception as e:
            ok(f"{msg}: {e}")

    try:
        # TEST 1
        print_header("TEST 1: File Creation")
        client.create(f_main)
        ok(f"Created file: {f_main}")

        # TEST 2
        print_header("TEST 2: Duplicate Creation Rejected")
        expect_exception("Duplicate create blocked", lambda: client.create(f_main))

        # TEST 3
        print_header("TEST 3: Invalid Mode Rejected")
        expect_exception("Invalid mode blocked", lambda: client.open(f_main, mode="x"))

        # TEST 4
        print_header("TEST 4: Basic Write + Close")
        h = client.open(f_main, mode="w")
        payload = b"Line 1: 2, 3, 5, 7\n"
        written = client.write(h, offset=0, data=payload)
        client.close(h)
        if written == len(payload):
            ok("Wrote expected number of bytes")
        else:
            fail(f"Unexpected bytes written: {written}")

        # TEST 5
        print_header("TEST 5: Basic Read Back")
        h = client.open(f_main, mode="r")
        data = client.read(h, offset=0, length=64)
        client.close(h)
        if data == b"Line 1: 2, 3, 5, 7\n":
            ok("Read data matches written data")
        else:
            fail(f"Read mismatch: {data}")

        # TEST 6
        print_header("TEST 6: Cache Hit Verification")
        v_before = client.cache_metadata[f_main]["version"]
        h = client.open(f_main, mode="r")
        _ = client.read(h, offset=0, length=4)
        client.close(h)
        v_after = client.cache_metadata[f_main]["version"]
        if v_before == v_after:
            ok(f"Cache version stable at v{v_after}")
        else:
            fail(f"Cache version changed unexpectedly: {v_before} -> {v_after}")

        # TEST 7
        print_header("TEST 7: Offset and Chunked Writes")
        client.create(f_chunks)
        h = client.open(f_chunks, mode="w")
        client.write(h, offset=0, data=b"CHUNK_A|")
        client.write(h, offset=16, data=b"CHUNK_C|")
        client.write(h, offset=8, data=b"CHUNK_B|")
        client.close(h)

        h = client.open(f_chunks, mode="r")
        full = client.read(h, offset=0, length=24)
        middle = client.read(h, offset=8, length=8)
        client.close(h)
        if full == b"CHUNK_A|CHUNK_B|CHUNK_C|" and middle == b"CHUNK_B|":
            ok("Offset chunk write/read is correct")
        else:
            fail(f"Offset/chunk mismatch. full={full}, middle={middle}")

        # TEST 8
        print_header("TEST 8: String Write Auto-Encode")
        client.create(f_str)
        h = client.open(f_str, mode="w")
        n = client.write(h, offset=0, data="hello-string")
        client.close(h)
        h = client.open(f_str, mode="r")
        sdata = client.read(h, offset=0, length=64)
        client.close(h)
        if n == len("hello-string") and sdata == b"hello-string":
            ok("String input encoded and stored correctly")
        else:
            fail(f"String write/read mismatch. n={n}, data={sdata}")

        # TEST 9
        print_header("TEST 9: Read Beyond EOF")
        h = client.open(f_str, mode="r")
        eof_data = client.read(h, offset=1000, length=10)
        client.close(h)
        if eof_data == b"":
            ok("Reading beyond EOF returns empty bytes")
        else:
            fail(f"Expected empty bytes, got: {eof_data}")

        # TEST 10
        print_header("TEST 10: Missing File Open Rejected")
        expect_exception(
            "Opening non-existent file blocked",
            lambda: client.open(f"does_not_exist_{run_id}.txt", mode="r"),
        )

        # TEST 11
        print_header("TEST 11: Invalid Handle Read Rejected")
        expect_exception("Invalid handle read blocked", lambda: client.read(9999, 0, 10))

        # TEST 12
        print_header("TEST 12: Invalid Handle Write Rejected")
        expect_exception("Invalid handle write blocked", lambda: client.write(9999, 0, b"x"))

        # TEST 13
        print_header("TEST 13: Invalid Handle Close Rejected")
        expect_exception("Invalid handle close blocked", lambda: client.close(9999))

        # TEST 14
        print_header("TEST 14: Version Increment on Write-Close")
        client.create(f_ver)
        v1 = client.test_version_number(f_ver)
        h = client.open(f_ver, mode="w")
        client.write(h, 0, b"v2")
        client.close(h)
        v2 = client.test_version_number(f_ver)
        h = client.open(f_ver, mode="w")
        client.write(h, 0, b"v3")
        client.close(h)
        v3 = client.test_version_number(f_ver)

        if v1 == 1 and v2 == 2 and v3 == 3:
            ok(f"Version increments correct: {v1} -> {v2} -> {v3}")
        else:
            fail(f"Unexpected versions: {v1}, {v2}, {v3}")

        # TEST 15
        print_header("TEST 15: Overwrite at Offset Preserves Other Bytes")
        client.create(f_overwrite)
        h = client.open(f_overwrite, mode="w")
        client.write(h, 0, b"ABCDEF")
        client.close(h)

        h = client.open(f_overwrite, mode="w")
        client.write(h, 2, b"ZZ")
        client.close(h)

        h = client.open(f_overwrite, mode="r")
        final_data = client.read(h, 0, 64)
        client.close(h)
        if final_data == b"ABZZEF":
            ok("Offset overwrite behavior is correct")
        else:
            fail(f"Offset overwrite mismatch: {final_data}")

        # --- Task 2: Fault Tolerance Tests ---

        # TEST 16: Idempotent Create
        print_header("TEST 16: Idempotent Create (Same seq_num)")
        f_idem_create = f"idem_create_{run_id}.txt"
        # First create normally
        client.create(f_idem_create)
        # Now replay the same (client_id, seq_num) by manually calling _call_rpc
        last_seq = client.seq_num  # this is the seq_num used for the create above
        try:
            response = client._call_rpc("create", filename=f_idem_create,
                                        client_id=client.client_id, seq_num=last_seq)
            # Should return the cached success response, NOT "File already exists"
            if response.get("success"):
                ok("Idempotent create returned cached success (no duplicate error)")
            else:
                fail(f"Idempotent create failed: {response.get('error_message')}")
        except Exception as e:
            fail(f"Idempotent create raised exception: {e}")

        # TEST 17: Idempotent Close (version increments only once)
        print_header("TEST 17: Idempotent Close (Same seq_num)")
        f_idem_close = f"idem_close_{run_id}.txt"
        client.create(f_idem_close)
        h = client.open(f_idem_close, mode="w")
        client.write(h, 0, b"idem_data")
        # Close normally — this increments version and uses a seq_num
        client.close(h)
        v_after_first_close = client.test_version_number(f_idem_close)
        # Replay the same close seq_num
        close_seq = client.seq_num  # seq_num used by the close above (before test_version_number)
        # We need the seq used by close, which was seq_num - 1 since test_version_number
        # doesn't increment seq_num (it's read-only)
        try:
            response = client._call_rpc("close", file_handle=h, file_data=b"idem_data",
                                        client_id=client.client_id, seq_num=close_seq)
            v_after_replay = client.test_version_number(f_idem_close)
            if v_after_first_close == v_after_replay:
                ok(f"Version stayed at v{v_after_replay} after replayed close")
            else:
                fail(f"Version changed on replay: {v_after_first_close} -> {v_after_replay}")
        except Exception as e:
            fail(f"Idempotent close raised exception: {e}")

        # TEST 18: Retry on Socket Disconnect
        print_header("TEST 18: Retry on Socket Disconnect")
        f_retry = f"retry_test_{run_id}.txt"
        client.create(f_retry)
        # Forcefully break the socket to simulate a disconnect
        client.sock.close()
        client.sock = None
        try:
            # This should trigger retry logic: reconnect + resend
            h = client.open(f_retry, mode="w")
            client.write(h, 0, b"survived_disconnect")
            client.close(h)
            # Verify data
            h = client.open(f_retry, mode="r")
            data = client.read(h, 0, 64)
            client.close(h)
            if data == b"survived_disconnect":
                ok("Client recovered from socket disconnect and completed operation")
            else:
                fail(f"Data mismatch after disconnect recovery: {data}")
        except Exception as e:
            fail(f"Retry on disconnect failed: {e}")

    finally:
        client.disconnect()
        print("\n[SYSTEM] Client disconnected happily.")
        print(f"[SUMMARY] Passed: {passed} | Failed: {failed}")


if __name__ == "__main__":
    run_exhaustive_tests()
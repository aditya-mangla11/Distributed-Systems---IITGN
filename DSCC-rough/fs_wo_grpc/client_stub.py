import socket
import pickle
import struct
import os
import base64
import hashlib
import time
import uuid
from cryptography.fernet import Fernet, InvalidToken

PASSPHRASE = b"VERY_SECRET_KEY_BETWEEN_SERVER_AND_ALL_CLIENTS"
SECRET_KEY = base64.urlsafe_b64encode(hashlib.sha256(PASSPHRASE).digest())
cipher = Fernet(SECRET_KEY)


def send_msg(sock, msg_dict):
    raw_bytes = pickle.dumps(msg_dict)
    encrypted_bytes = cipher.encrypt(raw_bytes)
    length = struct.pack("!Q", len(encrypted_bytes))
    sock.sendall(length + encrypted_bytes)


def recvall(sock, n):
    data = bytearray()
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            return None
        data.extend(packet)
    return bytes(data)


def recv_msg(sock):
    raw_msglen = recvall(sock, 8)
    if not raw_msglen:
        return None
    msglen = struct.unpack("!Q", raw_msglen)[0]
    encrypted_data = recvall(sock, msglen)
    if not encrypted_data:
        return None
    raw_bytes = cipher.decrypt(encrypted_data)
    return pickle.loads(raw_bytes)


CLIENT_CACHE_DIR = "./temp_cache_files"
os.makedirs(CLIENT_CACHE_DIR, exist_ok=True)


class SecureFSClientStub:
    def __init__(self, host="localhost", port=50051, max_retries=3):
        self.host = host
        self.port = port
        self.sock = None
        self.timeout = 5.0

        self.active_handles = {}
        self.cache_metadata = {}

        # --- Task 2: Idempotency + Retry ---
        self.client_id = str(uuid.uuid4())
        self.seq_num = 0
        self.max_retries = max_retries

        self.connect()

    def _cache_path(self, filename, version):
        return os.path.join(CLIENT_CACHE_DIR, f"{filename}_v{version}.bin")

    def _delete_old_cached_versions(self, filename, keep_path=None):
        prefix = f"{filename}_v"
        suffix = ".bin"
        keep_abs = os.path.abspath(keep_path) if keep_path else None

        for entry in os.listdir(CLIENT_CACHE_DIR):
            if entry.startswith(prefix) and entry.endswith(suffix):
                path = os.path.join(CLIENT_CACHE_DIR, entry)
                if keep_abs is None or os.path.abspath(path) != keep_abs:
                    try:
                        os.remove(path)
                    except FileNotFoundError:
                        pass

    def connect(self):
        if self.sock:
            self.sock.close()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect((self.host, self.port))

    def _call_rpc(self, method, **kwargs):
        request = {"method": method, "args": kwargs}
        last_error = None

        # --- Task 2: Retry with exponential backoff ---
        for attempt in range(self.max_retries + 1):
            try:
                send_msg(self.sock, request)
                response = recv_msg(self.sock)
                if not response:
                    raise ConnectionError("Server disconnected.")
                if not response.get("success"):
                    # Application-level error — do NOT retry
                    raise Exception(f"RPC {method} failed: {response.get('error_message')}")
                return response

            except (socket.timeout, socket.error, ConnectionError, EOFError,
                    InvalidToken, AttributeError, OSError) as e:
                last_error = e
                if attempt < self.max_retries:
                    backoff = 2 ** attempt  # 1s, 2s, 4s
                    print(f"####[RETRY {attempt+1}/{self.max_retries}] {type(e).__name__}: {e}. "
                          f"Reconnecting in {backoff}s...")
                    time.sleep(backoff)
                    try:
                        self.connect()
                    except Exception as conn_err:
                        print(f"####[RECONNECT FAILED] {conn_err}")
                        continue
                else:
                    raise ConnectionError(
                        f"RPC {method} failed after {self.max_retries} retries: {last_error}"
                    ) from last_error

    def _next_seq(self):
        """Increment and return the next sequence number for mutating RPCs."""
        self.seq_num += 1
        return self.seq_num

    def create(self, filename):
        seq = self._next_seq()
        response = self._call_rpc("create", filename=filename,
                                  client_id=self.client_id, seq_num=seq)
        return response["file_handle"]

    def test_version_number(self, filename):
        response = self._call_rpc("test_version_number", filename=filename)
        return response["version"]

    def open(self, filename, mode="r"):
        if mode not in ["r", "w"]:
            raise ValueError("Mode must be 'r' or 'w'")

        fetch_data = True
        local_version = 0

        if filename in self.cache_metadata:
            local_version = self.cache_metadata[filename]["version"]
            server_version = self.test_version_number(filename)

            if server_version == local_version:
                cached_path = self.cache_metadata[filename]["path"]
                if os.path.exists(cached_path):
                    fetch_data = False
                    print(
                        f"$$$$[CACHE HIT] {filename} is up-to-date (v{local_version}). \nSkipping network download."
                    )
                else:
                    print(f"$$$$[CACHE MISS] {filename} metadata exists but cache file missing. Fetching...")
            else:
                print(f"$$$$[CACHE MISS] {filename} outdated \n(Local: v{local_version}, Server: v{server_version}). Fetching...")

        response = self._call_rpc("open", filename=filename, mode=mode, fetch_data=fetch_data)
        handle = response["file_handle"]
        server_version = response["server_version"]

        local_path = self._cache_path(filename, server_version)

        if fetch_data:
            with open(local_path, "wb") as f:
                f.write(response["file_data"])
            self.cache_metadata[filename] = {"version": server_version, "path": local_path}
            self._delete_old_cached_versions(filename, keep_path=local_path)
        else:
            local_path = self.cache_metadata[filename]["path"]

        self.active_handles[handle] = {"path": local_path, "filename": filename}
        return handle

    def read(self, file_handle, offset, length):
        if file_handle not in self.active_handles:
            raise Exception("Invalid file handle")

        local_path = self.active_handles[file_handle]["path"]
        with open(local_path, "rb") as f:
            f.seek(offset)
            return f.read(length)

    def write(self, file_handle, offset, data):
        if file_handle not in self.active_handles:
            raise Exception("Invalid file handle")

        if isinstance(data, str):
            data = data.encode("utf-8")

        local_path = self.active_handles[file_handle]["path"]
        with open(local_path, "r+b") as f:
            f.seek(offset)
            f.write(data)
            return len(data)

    def close(self, file_handle):
        if file_handle not in self.active_handles:
            raise Exception("Invalid file handle")

        file_info = self.active_handles.pop(file_handle)
        local_path = file_info["path"]
        filename = file_info["filename"]

        with open(local_path, "rb") as f:
            final_data = f.read()

        seq = self._next_seq()
        response = self._call_rpc("close", file_handle=file_handle, file_data=final_data,
                                  client_id=self.client_id, seq_num=seq)

        if "new_version" in response:
            new_version = response["new_version"]
            new_path = self._cache_path(filename, new_version)

            if os.path.abspath(local_path) != os.path.abspath(new_path):
                if os.path.exists(new_path):
                    os.remove(new_path)
                os.replace(local_path, new_path)

            self.cache_metadata[filename] = {"version": new_version, "path": new_path}
            self._delete_old_cached_versions(filename, keep_path=new_path)

        return response["success"]

    def disconnect(self):
        if self.sock:
            self.sock.close()
            self.sock = None
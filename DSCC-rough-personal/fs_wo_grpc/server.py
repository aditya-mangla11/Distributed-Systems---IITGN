import os
import threading
from server_stub import SecureRPCServer

BASE_DIR = "./server_data"
os.makedirs(BASE_DIR, exist_ok=True)

class AFSLogic:
    def __init__(self):
        self.open_files = {}  
        self.file_versions = {} 
        self.next_handle = 1
        self.state_lock = threading.Lock()

        # --- Task 2: Idempotency dedup cache ---
        # Maps client_id -> (last_seq_num, cached_response)
        self.dedup_cache = {}

    def _get_full_path(self, filename):
        return os.path.join(BASE_DIR, filename)

    def test_version_number(self, filename):
        with self.state_lock:
            version = self.file_versions.get(filename, 1) 
        return {"version": version, "success": True, "error_message": ""}

    # --- Task 2: Idempotency check helper ---
    def _check_idempotent(self, client_id, seq_num):
        """Returns cached response if this is a duplicate request, else None."""
        if client_id is None or seq_num is None:
            return None
        with self.state_lock:
            if client_id in self.dedup_cache:
                last_seq, cached_resp = self.dedup_cache[client_id]
                if seq_num <= last_seq:
                    print(f"[DEDUP] Duplicate detected: client={client_id}, seq={seq_num} (last={last_seq})")
                    return cached_resp
        return None

    def _cache_response(self, client_id, seq_num, response):
        """Cache a response for idempotency dedup."""
        if client_id is not None and seq_num is not None:
            with self.state_lock:
                self.dedup_cache[client_id] = (seq_num, response)

    def create_file(self, filename, client_id=None, seq_num=None):
        # --- Task 2: Check for duplicate request ---
        cached = self._check_idempotent(client_id, seq_num)
        if cached is not None:
            return cached

        filepath = self._get_full_path(filename)
        if os.path.exists(filepath):
            return {"file_handle": -1, "success": False, "error_message": "File already exists"}
        
        try:
            open(filepath, 'wb').close()
            with self.state_lock:
                self.file_versions[filename] = 1
            response = {"file_handle": 0, "success": True, "error_message": ""}
            self._cache_response(client_id, seq_num, response)
            return response
        except Exception as e:
            return {"file_handle": -1, "success": False, "error_message": str(e)}

    def open_file(self, filename, mode, fetch_data=True):
        if mode not in ['r', 'w']:
            return {"file_handle": -1, "file_data": b"", "server_version": 0, "success": False, "error_message": "Mode must be 'r' or 'w'"}
            
        filepath = self._get_full_path(filename)
        
        try:
            if not os.path.exists(filepath) and mode == 'r':
                return {"file_handle": -1, "file_data": b"", "server_version": 0, "success": False, "error_message": "File not found"}
                
            file_data = b""
            if fetch_data and os.path.exists(filepath):
                with open(filepath, 'rb') as f:
                    file_data = f.read()

            with self.state_lock:
                handle = self.next_handle
                self.open_files[handle] = {"filename": filename, "mode": mode}
                self.next_handle += 1
                
                if filename not in self.file_versions and os.path.exists(filepath):
                    self.file_versions[filename] = 1
                server_version = self.file_versions.get(filename, 1)

            return {"file_handle": handle, "file_data": file_data, "server_version": server_version, "success": True, "error_message": ""}
        except Exception as e:
            return {"file_handle": -1, "file_data": b"", "server_version": 0, "success": False, "error_message": str(e)}

    def close_file(self, file_handle, file_data, client_id=None, seq_num=None):
        # --- Task 2: Check for duplicate request ---
        cached = self._check_idempotent(client_id, seq_num)
        if cached is not None:
            return cached

        with self.state_lock:
            file_info = self.open_files.pop(file_handle, None)

        if not file_info:
            return {"success": False, "error_message": "Invalid file handle"}

        try:
            filename = file_info['filename']
            with self.state_lock:
                current_version = self.file_versions.get(filename, 1)
            
            if file_info['mode'] == 'w':
                filepath = self._get_full_path(filename)
                with open(filepath, 'wb') as f:
                    f.write(file_data)
                
                with self.state_lock:
                    self.file_versions[filename] = current_version + 1
                    current_version = self.file_versions[filename]
                    
            response = {"success": True, "new_version": current_version, "error_message": ""}
            self._cache_response(client_id, seq_num, response)
            return response
        except Exception as e:
            return {"success": False, "error_message": str(e)}

if __name__ == '__main__':
    afs_logic = AFSLogic()
    server = SecureRPCServer(implementation=afs_logic)
    server.start()
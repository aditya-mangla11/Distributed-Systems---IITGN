import os
import threading
import grpc
from concurrent import futures
import fs_pb2
import fs_pb2_grpc

BASE_DIR = "./server_data"
os.makedirs(BASE_DIR, exist_ok=True)

class AFSServicer(fs_pb2_grpc.AFSFileSystemServicer):
    def __init__(self):
        self.open_files = {}  
        self.file_versions = {} 
        self.next_handle = 1
        self.state_lock = threading.Lock()

        # client_id : {last_seq_num, cached_response}
        self.dedup_cache = {}

    def _get_full_path(self, filename):
        return os.path.join(BASE_DIR, filename)

    # If seq_num <= last_seq, return cached response, else process normally.
    def _check_idempotent(self, client_id, seq_num):
        """Returns cached response if this is a duplicate request, else None."""
        if not client_id or seq_num <= 0:
            return None
        with self.state_lock:
            if client_id in self.dedup_cache:
                last_seq, cached_resp = self.dedup_cache[client_id]
                if seq_num <= last_seq:
                    print(f"[DEDUP] Duplicate detected: client={client_id}, seq={seq_num} (last={last_seq})")
                    return cached_resp
        return None

    # Cache the response for potential retries after successful processing
    def _cache_response(self, client_id, seq_num, response):
        """Cache a response for idempotency dedup."""
        if client_id and seq_num > 0:
            with self.state_lock:
                self.dedup_cache[client_id] = (seq_num, response)

    def TestVersionNumber(self, request, context):
        with self.state_lock:
            version = self.file_versions.get(request.filename, 1) 
        return fs_pb2.TestVersionResponse(version=version, success=True, error_message="")

    def Create(self, request, context):
        cached = self._check_idempotent(request.client_id, request.seq_num)
        if cached is not None:
            return cached

        filepath = self._get_full_path(request.filename)
        if os.path.exists(filepath):
            return fs_pb2.CreateResponse(file_handle=-1, success=False, error_message="File already exists")
        
        try:
            open(filepath, 'wb').close()
            with self.state_lock:
                self.file_versions[request.filename] = 1
            response = fs_pb2.CreateResponse(file_handle=0, success=True, error_message="")
            self._cache_response(request.client_id, request.seq_num, response)
            return response
        except Exception as e:
            return fs_pb2.CreateResponse(file_handle=-1, success=False, error_message=str(e))

    def Open(self, request, context):
        if request.mode not in ['r', 'w']:
            return fs_pb2.OpenResponse(file_handle=-1, success=False, error_message="Mode must be 'r' or 'w'")
            
        filepath = self._get_full_path(request.filename)
        
        try:
            if not os.path.exists(filepath) and request.mode == 'r':
                return fs_pb2.OpenResponse(file_handle=-1, success=False, error_message="File not found")
                
            file_data = b""
            if request.fetch_data and os.path.exists(filepath):
                with open(filepath, 'rb') as f:
                    file_data = f.read()

            with self.state_lock:
                handle = self.next_handle
                self.open_files[handle] = {"filename": request.filename, "mode": request.mode}
                self.next_handle += 1
                
                if request.filename not in self.file_versions and os.path.exists(filepath):
                    self.file_versions[request.filename] = 1
                server_version = self.file_versions.get(request.filename, 1)

            return fs_pb2.OpenResponse(
                file_handle=handle, 
                file_data=file_data, 
                server_version=server_version, 
                success=True
            )
        except Exception as e:
            return fs_pb2.OpenResponse(file_handle=-1, success=False, error_message=str(e))

    def Close(self, request, context):
        cached = self._check_idempotent(request.client_id, request.seq_num)
        if cached is not None:
            return cached

        with self.state_lock:
            file_info = self.open_files.pop(request.file_handle, None)

        if not file_info:
            return fs_pb2.CloseResponse(success=False, error_message="Invalid file handle")

        try:
            filename = file_info['filename']
            with self.state_lock:
                current_version = self.file_versions.get(filename, 1)
            
            new_version = 0
            if file_info['mode'] == 'w':
                filepath = self._get_full_path(filename)
                with open(filepath, 'wb') as f:
                    f.write(request.file_data)
                
                with self.state_lock:
                    self.file_versions[filename] = current_version + 1
                    new_version = self.file_versions[filename]
            
            response = fs_pb2.CloseResponse(success=True, new_version=new_version)
            self._cache_response(request.client_id, request.seq_num, response)
            return response
        except Exception as e:
            return fs_pb2.CloseResponse(success=False, error_message=str(e))

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    fs_pb2_grpc.add_AFSFileSystemServicer_to_server(AFSServicer(), server)
    server.add_insecure_port('[::]:50051')
    print("[STARTING] gRPC AFS Server listening on port 50051...")
    server.start()
    server.wait_for_termination()

if __name__ == '__main__':
    serve()
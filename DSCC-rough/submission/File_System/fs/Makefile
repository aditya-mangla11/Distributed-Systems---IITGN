PYTHON ?= python3

.PHONY: all proto install-deps run-server run-client test prime_test clean help

all: help

proto:
	$(PYTHON) -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. fs.proto

install-deps:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install grpcio grpcio-tools

run-server:
	$(PYTHON) server.py

run-client:
	$(PYTHON) client.py

prime_test:
	$(PYTHON) prime_pre_test.py
	$(PYTHON) client_prime.py input_dataset_1.txt input_dataset_2.txt input_dataset_3.txt input_dataset_4.txt input_dataset_5.txt

clean:
	rm -f fs_pb2.py fs_pb2_grpc.py
	rm -rf __pycache__
	rm -rf server_data temp_cache_files

help:
	@echo "Makefile targets:"
	@echo "  make proto        Generate gRPC python files from fs.proto"
	@echo "  make install-deps Install Python dependencies (grpc)"
	@echo "  make run-server   Start the gRPC server (blocks)"
	@echo "  make run-client   Run sample client script (connects to server)"
	@echo "  make prime_test   Prepare datasets and append unique primes into results.txt"
	@echo "  make test         Run unit tests (client_test.py)"
	@echo "  make clean        Remove generated files and caches"

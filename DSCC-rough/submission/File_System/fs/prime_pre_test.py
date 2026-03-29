from client_stub import PrimeFSClientStub

DATASETS = {
    "input_dataset_1.txt": [2, 3, 4, 5, 8, 11, 11, 12, 15, 17],
    "input_dataset_2.txt": [19, 21, 22, 23, 24, 29, 31, 31, 35, 40],
    "input_dataset_3.txt": [5, 6, 7, 9, 10, 13, 13, 17, 20, 21],
    "input_dataset_4.txt": [2, 14, 15, 19, 25, 27, 29, 33, 37, 37],
    "input_dataset_5.txt": [3, 4, 8, 11, 16, 18, 23, 23, 41, 42],
}



def ensure_file_with_content(client, filename, content):
    try:
        client.create(filename)
    except Exception:
        pass

    handle = client.open(filename, mode="w")
    try:
        client.write(handle, 0, content)
    finally:
        client.close(handle)



def main():
    client = PrimeFSClientStub()

    try:
        for filename, values in DATASETS.items():
            body = "\n".join(str(v) for v in values) + "\n"
            ensure_file_with_content(client, filename, body)
            print(f"[SETUP] Wrote {filename} with {len(values)} values")

        ensure_file_with_content(client, "results.txt", "")
        print("[SETUP] Ensured results.txt exists (reset to empty)")
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()

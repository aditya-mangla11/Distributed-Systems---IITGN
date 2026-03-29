# Prime File System (PrimeFS)

Here, the main design proprerties are:
- Similarity to AFS
- Operations are Open, Create, Read, Write, Close
- For now, we have a single server.
- **WE HAVE 2 VERSIONS:**
    - We are using Python with gRPC (Proto)
    - We made our own Pickling + Encryption Model
- Transactions are happening with Requests and Responses between Client and Server with Entire File transfered.

## Operationwise Details:

### Open:
    Gives Open Request:
        - Filename (Relative path to Server FOlder)
        - Mode like r, w
        - Fetch Data, (It is False if already cached). Just for server to keep a record that someone is working on a file.
    Receives Response:
        - With File Handle
        - File Data
        - Version Number
        - Sucess Indicator 
        - Error (If there)

### Create:
    Create Request:
        - File Name is given to be created
    
    Response:
        - Same as Open (Success message for checking duplicate also)

### Close:
    Request:
        - File Handle
        - Data
    Response:
        - Success
        - New version
        - Error Msg

### Read:
**DONE LOCALLY**
    Req:
        - File Handle
        - Offset
        - Length

### Write:
**DONE LOCALLY**
    Req:
        - FH
        - Offset
        - Data

### Test Version Number:
    Gives Open Request:
        - Filename (Relative path to Server FOlder)
    Receives Response:
        - Version Number
        - Sucess Indicator 
        - Error (If there)




## Client:
### File Storage
- Stored in the cache folder
### Read
- Happens locally, the offset and all the other things are handled in cache folder
### Write
- Happens Locally

## Server:
- Every file is stored and created in the folder server_data. 
- So, the path names are relative to that. NOT ABSOLUTE LIKE THAT AFS PAPAER.
- We will have 10 threads. I am currently running it locally.
- Here, I will create a file handle just as an int mapping to the file obj as a dict.
- If there are multiple clients access it, I will also add lock to that dict. (mutex lock)

### Open
- In open, we will have to only perform byte level ops.
- We will open the file and then return the file handle int as resp.
### Close
- In close, we will remove that from the fh dict.
- We will close the fole on folder and return the response.
### Create
- We check if the file exists or not and send the response.
- Similarly, we create a new fh and give resp.


# WITHOUT gRPC
Functioning is the same
## Instead of gRPC:
We package the content as a dict:
```python
{"version": version, "success": True, "error_message": ""}
```
- We use pickle to marshal and unmarshal it.
- We will then encrypt it using the FERNET encryption method.
- Since dataformat is fixed, this would work.

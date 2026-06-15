"""Upload a file to /upload — bypasses shell curl rewriting."""
import sys
import httpx

path = sys.argv[1]
url = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8077/upload"
with open(path, "rb") as f:
    r = httpx.post(url, files={"file": (path.split("/")[-1], f)}, timeout=300)
print("STATUS", r.status_code)
print(r.text)

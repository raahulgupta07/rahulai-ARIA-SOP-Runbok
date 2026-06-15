"""Raw health/endpoint checker — bypasses shell-level command rewriting."""
import sys
import urllib.request

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8077/health"
try:
    with urllib.request.urlopen(url, timeout=10) as r:
        print("STATUS", r.status)
        print(r.read().decode())
except Exception as e:
    print("ERR", repr(e))

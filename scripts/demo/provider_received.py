"""Demo: print the user message the fake provider received in the last request.

The fake provider (tests/e2e/fake_upstream.py, started by `make demo` on 127.0.0.1:9000)
records the exact bytes it gets; this shows what an LLM provider would have seen.
"""

import json
import sys
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:9000/_e2e/received", timeout=5) as response:
    received = json.load(response)
body = json.loads(received[-1]["body"])
users = [message for message in body["messages"] if message["role"] == "user"]
sys.stdout.buffer.write(f"{users[-1]['content']}\n".encode())

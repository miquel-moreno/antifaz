"""Demo client: the official OpenAI SDK pointed at Antifaz instead of at OpenAI.

Run by `make demo` (scripts/demo_gif.py), with Antifaz on localhost:8000 and a fake provider
behind it. It sends ticket.txt (synthetic data) and prints the answer it gets back.
"""

import os
import sys
from pathlib import Path

from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key=os.environ["ANTIFAZ_API_KEY"])
ticket = Path("ticket.txt").read_text(encoding="utf-8")
answer = client.chat.completions.create(
    model="demo", messages=[{"role": "user", "content": ticket}]
)
sys.stdout.buffer.write(f"{answer.choices[0].message.content}\n".encode())

#!/usr/bin/env python3
"""kauz_tunnel.py — one-shot tunnel manager: starts pinggy TCP tunnel,
parses tcp:// endpoint, saves to kz_tunnel.json. Re-run when tunnel expires (60 min free tier).

Requires: ssh (OpenSSH client) on PATH.
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "kz_tunnel.json")
LOCAL_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 2222

proc = subprocess.Popen(
    ["ssh", "-o", "StrictHostKeyChecking=no", "-p", "443",
     "-R0:localhost:%d" % LOCAL_PORT, "tcp@a.pinggy.io"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

deadline = time.time() + 60
endpoint = None
for _ in range(600):
    line = proc.stdout.readline()
    if not line:
        time.sleep(0.1)
        continue
    line = line.strip()
    print(line, flush=True)
    m = re.search(r"tcp://([\w.-]+):(\d+)", line)
    if m:
        endpoint = {"host": m.group(1), "port": int(m.group(2)),
                    "local_port": LOCAL_PORT, "started": int(time.time())}
        break
    if time.time() > deadline:
        break

if not endpoint:
    sys.exit("no tcp endpoint received")

with open(OUT, "w") as f:
    json.dump(endpoint, f, indent=2)
print(f"\nTUNNEL UP: {endpoint['host']}:{endpoint['port']} -> localhost:{LOCAL_PORT}")
print(f"saved -> {OUT}")
print("keeping ssh alive (Ctrl+C to stop)...")
try:
    proc.wait()
except KeyboardInterrupt:
    proc.kill()

#!/usr/bin/env python3
"""kauz_sshd.py — lightweight SSH server exposing THIS PC to Kauz AI agent.

Kauz's server-side ssh_* toolset (ssh_execute_command, ssh_list_directory,
ssh_read_file_content, ...) connects here through a public tunnel, letting
the cloud agent see and operate the local machine.

Auth: username/password (generate your own, pass via CLI or env).
Commands run through cmd.exe. Sessions are logged to kauz_sshd.log.

Usage:
  python kauz_sshd.py --port 2222 --user kauz --password 'ChangeMe123!'
Tunnel (separate terminal):
  ssh -R 0:localhost:2222 serveo.net      # or: ssh -R 2222:localhost:2222 localhost.run
"""
import argparse
import os
import socket
import subprocess
import sys
import threading
import time

import paramiko

HOST_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kauz_host_key")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kauz_sshd.log")

CONFIG = {"user": "", "password": "", "root": os.path.expanduser("~")}


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def ensure_host_key():
    if not os.path.exists(HOST_KEY_FILE):
        key = paramiko.RSAKey.generate(2048)
        key.write_private_key_file(HOST_KEY_FILE)
        log(f"generated host key -> {HOST_KEY_FILE}")
    return paramiko.RSAKey.from_private_key_file(HOST_KEY_FILE)


def run_command(cmd, cwd=None):
    cwd = cwd or CONFIG["root"]
    try:
        p = subprocess.run(["cmd.exe", "/c", cmd], capture_output=True,
                           timeout=60, cwd=cwd, shell=False)
        out = p.stdout.decode("cp866", errors="replace") if p.stdout else ""
        err = p.stderr.decode("cp866", errors="replace") if p.stderr else ""
        return out + ("\n" + err if err.strip() else ""), p.returncode
    except subprocess.TimeoutExpired:
        return "[timeout after 60s]", 124
    except Exception as e:
        return f"[exec error] {e}", 1


class Server(paramiko.ServerInterface):
    def __init__(self):
        self.event = threading.Event()

    def check_auth_password(self, username, password):
        ok = (username == CONFIG["user"] and password == CONFIG["password"])
        log(f"auth {'OK' if ok else 'FAIL'} user={username}")
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        self.event.set()
        return True

    def get_allowed_auths(self, username):
        return "password"


class ExecServer(Server):
    """Captures the exec command string."""
    def __init__(self):
        super().__init__()
        self.command = None

    def check_channel_exec_request(self, channel, command):
        self.command = command.decode("utf-8", errors="replace") if isinstance(command, bytes) else command
        channel.kz_command = self.command
        self.event.set()
        return True


def handle_client2(conn, addr, host_key):
    t = paramiko.Transport(conn)
    t.add_server_key(host_key)
    srv = ExecServer()
    try:
        t.start_server(server=srv)
    except Exception as e:
        log(f"transport error {addr}: {e}")
        return
    while t.is_active():
        chan = t.accept(20)
        if chan is None:
            break
        threading.Thread(target=serve_exec, args=(chan, addr), daemon=True).start()
    t.close()


def serve_exec(chan, addr):
    command = None
    for _ in range(100):
        command = getattr(chan, "kz_command", None)
        if command:
            break
        time.sleep(0.05)
    log(f"exec from {addr[0]}: {command!r}")
    if command:
        out, rc = run_command(command)
        chan.send(out.encode("utf-8", errors="replace"))
        chan.send_exit_status(rc)
    else:
        chan.send(b"kauz_sshd: exec only\n")
        chan.send_exit_status(0)
    chan.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=2222)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--user", default=os.environ.get("KAUZ_SSH_USER", "kauz"))
    ap.add_argument("--password", default=os.environ.get("KAUZ_SSH_PASS", ""))
    ap.add_argument("--root", default=os.environ.get("KAUZ_SSH_ROOT", os.path.expanduser("~")))
    args = ap.parse_args()
    if not args.password:
        sys.exit("set --password or KAUZ_SSH_PASS")
    CONFIG.update(user=args.user, password=args.password, root=args.root)
    host_key = ensure_host_key()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(10)
    log(f"kauz_sshd listening on {args.host}:{args.port} user={args.user} root={args.root}")
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle_client2, args=(conn, addr, host_key), daemon=True).start()


if __name__ == "__main__":
    main()

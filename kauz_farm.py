#!/usr/bin/env python3
"""kauz_farm.py — parallel mass-registration. One worker per live proxy.

Usage:
  python kauz_farm.py --total 30 --workers 3 \
    --imap-user x@gmail.com --imap-pass APPPASS \
    --proxy-list live_proxy.txt

Each worker grabs a fresh proxy assignment per account and calls the
proven register_one() from kauz_autoreg.py. Results append to kauz_accounts.jsonl.
"""
import argparse, json, os, random, sys, threading, time
import importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ka", os.path.join(HERE, "kauz_autoreg.py"))
ka = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ka)

lock = threading.Lock()
done = {"ok": 0, "fail": 0}

class Args:
    pass

def make_args(imap_user, imap_pass, proxy_list, mail_timeout, out, session):
    a = Args()
    a.imap_host = "imap.gmail.com"
    a.imap_user = imap_user
    a.imap_pass = imap_pass
    a.email_base = imap_user
    a.mail_timeout = mail_timeout
    a.proxy_list = proxy_list
    a.out = out
    a.session = session
    return a

def worker(idx, base_args, counter):
    while True:
        with lock:
            i = counter[0]
            if i >= counter[1]:
                return
            counter[0] += 1
            n = i + 1
        t0 = time.time()
        try:
            r = ka.register_one(base_args, f"w{idx}/{n}")
        except Exception as e:
            r = {"error": repr(e)[:120]}
        with lock:
            if r and r.get("session"):
                done["ok"] += 1
                print(f"[farm] #{n} OK {r['email']} ({time.time()-t0:.0f}s) | ok={done['ok']} fail={done['fail']}")
            else:
                done["fail"] += 1
                print(f"[farm] #{n} FAIL {json.dumps(r)[:150]} | ok={done['ok']} fail={done['fail']}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, default=10)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--imap-user", required=True)
    ap.add_argument("--imap-pass", required=True)
    ap.add_argument("--proxy-list", required=True)
    ap.add_argument("--mail-timeout", type=int, default=300)
    ap.add_argument("--out", default=os.path.join(HERE, "kauz_accounts.jsonl"))
    ap.add_argument("--session", default=os.path.join(HERE, "kauz_session_last.json"))
    args = ap.parse_args()

    base = make_args(args.imap_user, args.imap_pass, args.proxy_list,
                     args.mail_timeout, args.out, args.session)
    counter = [0, args.total]
    threads = []
    for i in range(args.workers):
        t = threading.Thread(target=worker, args=(i, base, counter), daemon=True)
        t.start(); threads.append(t)
    for t in threads:
        t.join()
    print(f"[farm] FINISHED ok={done['ok']} fail={done['fail']}")

if __name__ == "__main__":
    main()

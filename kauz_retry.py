#!/usr/bin/env python3
"""kauz_retry.py — re-verify accounts that signed up but didn't get their mail in time.
Parses farm logs for FAIL records (verified:false), polls IMAP once, verifies + signs in.
"""
import json, os, re, sys, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ka", os.path.join(HERE, "kauz_autoreg.py"))
ka = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ka)

PAT = re.compile(r'\{"email": "([^"]+)", "password": "([^"]+)"[^}]*"verified": false\}')
OUT = os.path.join(HERE, "kauz_accounts.jsonl")
SESS = os.path.join(HERE, "kauz_session_last.json")

IMAP_USER = os.environ.get("KAUZ_IMAP_USER")
IMAP_PASS = os.environ.get("KAUZ_IMAP_PASS")

cands = {}
for lg in sys.argv[1:] or [os.path.join(HERE, f) for f in os.listdir(HERE) if f.startswith("farm_batch") and f.endswith(".log")]:
    try:
        for m in PAT.finditer(open(lg, encoding="utf-8", errors="replace").read()):
            cands[m.group(1)] = m.group(2)
    except FileNotFoundError:
        pass

have = set()
if os.path.exists(OUT):
    for l in open(OUT, encoding="utf-8"):
        try: have.add(json.loads(l)["email"])
        except Exception: pass
cands = {e: p for e, p in cands.items() if e not in have}
print(f"candidates: {len(cands)}")

ok = 0
for mail, pwd in list(cands.items())[:40]:
    tok = ka.fetch_verify_token("imap.gmail.com", IMAP_USER, IMAP_PASS, mail, timeout=20)
    if not tok:
        print("no-token", mail[:40]); continue
    opener, jar = ka.build_opener()
    s, b = ka.verify_email(opener, tok)
    if s != 200:
        print("verify-fail", s, mail[:40], b[:60]); continue
    s, b = ka.sign_in(opener, mail, pwd)
    if s == 200:
        ka.save_account(OUT, SESS, mail, pwd, jar)
        ok += 1
        print("RESURRECTED", mail, f"({ok})")
    else:
        print("signin-fail", s, mail[:40], b[:80])
print(f"done: resurrected {ok}/{len(cands)}")

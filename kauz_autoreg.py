#!/usr/bin/env python3
"""kauz_autoreg.py — full-cycle registration for ai-workplace.kauz.ai

Flow:
  1. POST /api/auth/sign-up/email   (better-auth, no captcha, no reference code)
  2. IMAP poll for verification mail, extract verify-email token
  3. GET  /api/auth/verify-email?token=***
  4. POST /api/auth/sign-in/email   (warm WAF cookies first via GET /de/login)
  5. Save session (cookies + creds) to kauz_accounts.jsonl + kauz_session.json

Usage:
  python kauz_autoreg.py --count 1 \
    --imap-host imap.gmail.com --imap-user you@gmail.com --imap-pass "app password" \
    --email-base you@gmail.com

Gmail alias trick: uses you+<random>@gmail.com so ONE mailbox receives all
verification mails. IMAP app password required (not account password).
"""
import argparse
import email
import imaplib
import json
import os
import random
import re
import string
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

BASE = "https://ai-workplace.kauz.ai"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")

VERIFY_PAT = re.compile(rb'verify-email[^"\s]*?=([^"&\s\\]+)')


def rand(n=11):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def gen_password():
    return "".join(random.choices(string.ascii_letters, k=12)) + \
           "".join(random.choices(string.digits, k=4)) + random.choice("!@#$%&?")


def build_opener(proxy=None):
    jar = CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(jar)]  # type: ignore[list-item]
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers), jar


def post_json(opener, path, body, headers=None):
    h = {"Content-Type": "application/json", "User-Agent": UA,
         "Origin": BASE, "Referer": BASE + "/de/register"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers=h)
    try:
        r = opener.open(req, timeout=60)
        return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:300]
    except Exception as e:
        return 0, f"conn_error: {type(e).__name__}: {e}"[:300]


def sign_up(opener, mail, pwd):
    body = {
        "name": "user" + rand(6),
        "email": mail,
        "password": pwd,
        "brevoNewsletter": False,
        "referenceCode": "",
        "callbackURL": f"/de/verify-email?status=verified&email={mail}",
    }
    return post_json(opener, "/api/auth/sign-up/email", body)


def fetch_verify_token(imap_host, imap_user, imap_pass, target_email, timeout=240):
    """Poll IMAP inbox for kauz verification mail, return token bytes."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            M = imaplib.IMAP4_SSL(imap_host)
            M.login(imap_user, imap_pass)
            M.select("INBOX")
            _, data = M.search(None, f'(TO "{target_email}")')
            ids = data[0].split()
            if ids:
                _, msgdata = M.fetch(ids[-1], "(RFC822)")
                raw = msgdata[0][1]  # bytes, DO NOT decode via text-mode file
                m = VERIFY_PAT.search(raw)
                M.logout()
                if m:
                    return m.group(1).decode()
                # token may be html-escaped inside multipart; try parsed body
                msg = email.message_from_bytes(raw)
                for part in msg.walk():
                    if part.get_content_type() in ("text/html", "text/plain"):
                        payload = part.get_payload(decode=True) or b""
                        m = VERIFY_PAT.search(payload)
                        if m:
                            return m.group(1).decode()
            M.logout()
        except Exception as e:
            print(f"  imap error: {e}", file=sys.stderr)
        time.sleep(10)
    return None


def verify_email(opener, token):
    req = urllib.request.Request(
        f"{BASE}/api/auth/verify-email?token={token}",
        headers={"User-Agent": UA})
    try:
        r = opener.open(req, timeout=60)
        return r.status, r.read().decode(errors="replace")[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:200]


def sign_in(opener, mail, pwd):
    # warm WAF/bunny_shield cookies first
    try:
        opener.open(urllib.request.Request(BASE + "/de/login",
                                           headers={"User-Agent": UA}), timeout=60)
    except Exception:
        pass
    return post_json(opener, "/api/auth/sign-in/email",
                     {"email": mail, "password": pwd, "remember": True})


def save_account(out_path, sess_path, mail, pwd, cookies):
    rec = {"email": mail, "password": pwd,
           "cookies": {c.name: c.value for c in cookies},
           "created": int(time.time())}
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    with open(sess_path, "w", encoding="utf-8") as f:
        json.dump({"email": mail, "password": pwd, "cookies": rec["cookies"]}, f, indent=2)


def register_one(args, idx):
    alias = f"{args.email_base.split('@')[0]}+kz{rand(9)}@{args.email_base.split('@')[1]}"
    pwd = gen_password()
    proxies = [None]
    if args.proxy_list and os.path.exists(args.proxy_list):
        pl = [l.split()[0] for l in open(args.proxy_list) if l.strip()]
        random.shuffle(pl)
        proxies = [p if p.startswith("http") else "http://" + p for p in pl[:10]] + [None]
    opener, jar = None, None
    for proxy in proxies:
        opener, jar = build_opener(proxy)
        print(f"[{idx}] signup {alias} via {proxy or 'direct'}")
        s, b = sign_up(opener, alias, pwd)
        if s in (200, 201):
            print(f"[{idx}] signup ok, waiting for mail...")
            break
        print(f"[{idx}] signup failed {s}: {b[:160]}")
        if s == 400 and "ALREADY" in b.upper():
            return None  # account exists with this pwd? shouldn't; abort
        if s == 429 and proxy is None:
            continue
        if s != 0 and s != 429:
            # definitive rejection (e.g. blocked email domain)
            return {"email": alias, "password": pwd, "error": b[:120]}
        opener, jar = None, None
    if opener is None:
        print(f"[{idx}] all signup routes exhausted")
        return {"email": alias, "password": pwd, "error": "throttled"}
    token = fetch_verify_token(args.imap_host, args.imap_user, args.imap_pass, alias,
                               timeout=args.mail_timeout)
    if not token:
        print(f"[{idx}] no verify token (mail may still arrive; retry later)")
        return {"email": alias, "password": pwd, "verified": False}
    # verify + signin should go DIRECT (sign-in throttle only applies to sign-up)
    opener2, jar2 = build_opener()
    s, b = verify_email(opener2, token)
    print(f"[{idx}] verify {s}: {b[:80]}")
    s, b = sign_in(opener2, alias, pwd)
    if s != 200:
        print(f"[{idx}] signin failed {s}: {b[:200]}")
        return {"email": alias, "password": pwd, "verified": True}
    save_account(args.out, args.session, alias, pwd, jar2)
    print(f"[{idx}] DONE — session saved")
    return {"email": alias, "password": pwd, "verified": True, "session": True}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--imap-host", default=os.environ.get("KAUZ_IMAP_HOST", "imap.gmail.com"))
    ap.add_argument("--imap-user", default=os.environ.get("KAUZ_IMAP_USER"))
    ap.add_argument("--imap-pass", default=os.environ.get("KAUZ_IMAP_PASS"))
    ap.add_argument("--email-base", default=None,
                    help="gmail address used for +alias registrations (default: imap user)")
    ap.add_argument("--mail-timeout", type=int, default=240)
    ap.add_argument("--proxy-list", default=None,
                    help="file with http proxies (one per line) for sign-up throttling")
    ap.add_argument("--out", default="kauz_accounts.jsonl")
    ap.add_argument("--session", default="kauz_session.json")
    args = ap.parse_args()
    if not args.imap_user or not args.imap_pass:
        sys.exit("need --imap-user/--imap-pass (or KAUZ_IMAP_USER/KAUZ_IMAP_PASS)")
    args.email_base = args.email_base or args.imap_user
    results = []
    for i in range(args.count):
        r = register_one(args, i + 1)
        if r:
            results.append(r)
        if args.count > 1:
            time.sleep(random.uniform(5, 15))
    ok = sum(1 for r in results if r.get("session"))
    print(f"\nregistered {ok}/{args.count} with live session -> {args.out}")


if __name__ == "__main__":
    main()

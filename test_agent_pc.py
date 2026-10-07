import json, importlib.util
spec = importlib.util.spec_from_file_location("kb", "kauz_bridge.py")
kb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kb)
sess = json.load(open("kauz_session.json"))
kb.STATE["cookie"] = "; ".join(f"{k}={v}" for k, v in sess["cookies"].items())
u, p = [l.strip() for l in open("kz_cred.txt").read().splitlines()[:2]]
prompt = (
    f"Use the ssh_execute_command tool to connect to host "
    f"tvjar-178-150-68-140.run.pinggy-free.link port 35391 username {u} password {p} "
    f"and run command: whoami && hostname && echo AGENT-SEES-PC. Report the output."
)
for line in kb.kauz_stream(prompt, "kauz-selection", ["ssh"]):
    if '"type"' in line:
        try:
            ev = json.loads(line[5:].strip())
            t = ev.get("type", "")
            if t == "tool-input-available":
                print("TOOL-INPUT:", json.dumps(ev.get("input"), ensure_ascii=False)[:300])
            elif t == "tool-output-available":
                print("TOOL-OUTPUT:", json.dumps(ev.get("output"), ensure_ascii=False)[:500])
            elif t == "text-delta":
                pass
        except Exception:
            pass

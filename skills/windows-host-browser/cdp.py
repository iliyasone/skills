#!/usr/bin/env python3
"""Talk to the Proxy Switcher extension in the host's debug Chrome over CDP.

Endpoint defaults to http://127.0.0.1:18800 — the fixed local port that
connect.sh (next to this file) keeps pointed at the browser. Run connect.sh
first; override with $CDP_HTTP only if you are somewhere connect.sh does not
run (see SKILL.md — the Windows-side debug port itself is not fixed).

Two gotchas this file already handles, learned the hard way:
  * Chrome rejects the websocket unless the Origin header is absent
    (suppress_origin=True), because the browser was started without
    --remote-allow-origins.
  * The extension is MV3, so its service worker sleeps when idle and drops out
    of /json. We wake it by opening its popup page as a target, then attach.

Requires: pip install websocket-client  (present on dev-remote and the WSL box).

Subcommands:
  get                 print the current browser-wide proxy setting
  direct              clear the proxy (back to a direct connection)
  set HOST PORT [SCHEME] [USER] [PASS]   apply a fixed proxy (scheme default http)
  egress              fetch ip-api.com through the current proxy to prove egress
  eval "<js expr>"    run an expression in the extension service-worker context
"""
import json, os, sys, time, urllib.request
import websocket  # websocket-client

EXTID = "iejkjpdckomcjdhmkemlfdapjodcpgih"  # Proxy Switcher
BASE = os.environ.get("CDP_HTTP", "http://127.0.0.1:18800")


def _http(path):
    return json.load(urllib.request.urlopen(BASE + path, timeout=5))


def _rpc_factory(ws):
    counter = {"i": 0}

    def rpc(method, params=None, sid=None):
        counter["i"] += 1
        mid = counter["i"]
        msg = {"id": mid, "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        ws.send(json.dumps(msg))
        while True:
            r = json.loads(ws.recv())
            if r.get("id") == mid:
                if "error" in r:
                    raise RuntimeError(r["error"])
                return r.get("result", {})

    return rpc


def sw_eval(expression):
    """Evaluate a JS expression inside the extension's service worker.

    Wrap awaited work in an async IIFE — the eval context has no top-level await.
    """
    browser_ws = _http("/json/version")["webSocketDebuggerUrl"]
    ws = websocket.create_connection(browser_ws, max_size=None, suppress_origin=True)
    rpc = _rpc_factory(ws)

    def find_sw():
        for t in rpc("Target.getTargets")["targetInfos"]:
            if t["type"] == "service_worker" and EXTID in t["url"]:
                return t["targetId"]
        return None

    tid = find_sw()
    if not tid:
        rpc("Target.createTarget", {"url": f"chrome-extension://{EXTID}/data/popup/popup.html"})
        for _ in range(15):
            time.sleep(0.4)
            tid = find_sw()
            if tid:
                break
    if not tid:
        ws.close()
        raise SystemExit("extension service worker not reachable — is the right Chrome profile running?")

    sid = rpc("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
    rpc("Runtime.enable", {}, sid)
    res = rpc("Runtime.evaluate",
              {"expression": expression, "awaitPromise": True, "returnByValue": True}, sid)
    ws.close()
    r = res.get("result", {})
    if r.get("subtype") == "error":
        raise SystemExit("JS error: " + r.get("description", str(r)))
    return r.get("value", r)


GET = "(async()=>JSON.stringify(await chrome.proxy.settings.get({})))()"
DIRECT = ("(async()=>{await chrome.proxy.settings.set({scope:'regular',value:{mode:'direct'}});"
          "await chrome.storage.local.set({'auth-active':false});"
          "return JSON.stringify((await chrome.proxy.settings.get({})).value);})()")

def _set_js(host, port, scheme, user, pw):
    auth = ""
    if user:
        auth = (f"await chrome.storage.local.set({{'auth-active':true,"
                f"'auth-username':{json.dumps(user)},'auth-password':{json.dumps(pw or '')}}});"
                "try{core.action.webrequest.initiate();}catch(e){}")
    return ("(async()=>{" + auth +
            "await chrome.proxy.settings.set({scope:'regular',value:{mode:'fixed_servers',rules:{"
            f"singleProxy:{{scheme:{json.dumps(scheme)},host:{json.dumps(host)},port:{int(port)}}},"
            "bypassList:['localhost','127.0.0.1']}}});"
            "return JSON.stringify((await chrome.proxy.settings.get({})).value);})()")

EGRESS = ("(async()=>{try{core.action.webrequest.initiate();}catch(e){}"
          "const c=new AbortController();const t=setTimeout(()=>c.abort(),12000);"
          "try{const r=await fetch('http://ip-api.com/json/?fields=query,country,city',"
          "{signal:c.signal,cache:'no-store'});clearTimeout(t);"
          "return JSON.stringify(await r.json());}"
          "catch(e){clearTimeout(t);return JSON.stringify({error:String(e)});}})()")


def main(argv):
    cmd = argv[0] if argv else "get"
    if cmd == "get":
        print(sw_eval(GET))
    elif cmd == "direct":
        print(sw_eval(DIRECT))
    elif cmd == "egress":
        print(sw_eval(EGRESS))
    elif cmd == "set":
        host, port = argv[1], argv[2]
        scheme = argv[3] if len(argv) > 3 else "http"
        user = argv[4] if len(argv) > 4 else ""
        pw = argv[5] if len(argv) > 5 else ""
        print(sw_eval(_set_js(host, port, scheme, user, pw)))
    elif cmd == "eval":
        print(sw_eval(argv[1]))
    else:
        print(__doc__)
        raise SystemExit(2)


if __name__ == "__main__":
    main(sys.argv[1:])

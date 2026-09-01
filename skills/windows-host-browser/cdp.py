#!/usr/bin/env python3
"""Talk to the Proxy Switcher extension in the host's debug Chrome over CDP.

Endpoint defaults to http://127.0.0.1:18800 — the fixed local port that
connect.sh (next to this file) keeps pointed at the browser. Run connect.sh
first; override with $CDP_HTTP only if you are somewhere connect.sh does not
run (see SKILL.md — the Windows-side debug port itself is not fixed).

The browser can run several Chrome profiles at once. Each profile has its own
windows, cookies, copy of the extension — and therefore its own proxy setting.
Profiles surface in CDP as distinct browserContextIds; this tool tells them
apart by the pages they have open. With one profile running, commands work as
before; with several you must pick one (see `profiles` / --profile below).

Gotchas this file already handles, learned the hard way:
  * Chrome rejects the websocket unless the Origin header is absent
    (suppress_origin=True), because the browser was started without
    --remote-allow-origins.
  * The extension is MV3, so its service worker sleeps when idle and drops out
    of the target list. Waking the right profile's worker matters:
    Target.createTarget cannot address a real profile's browserContextId, so we
    window.open an about:blank tab from one of the profile's own pages, CDP-
    navigate it to the extension popup (privileged, so allowed), then close it.

Requires: pip install websocket-client  (present on dev-remote and the WSL box).

Subcommands (all but `profiles` accept --profile / -p SELECTOR):
  profiles            list running profiles: index, proxy state, open tabs
  get                 print the profile's current proxy setting
  direct              clear the proxy (back to a direct connection)
  set HOST PORT [SCHEME] [USER] [PASS]   apply a fixed proxy (scheme default http)
  egress              fetch ip-api.com through the current proxy to prove egress
  eval "<js expr>"    run an expression in the extension service-worker context

SELECTOR is a 1-based index from `profiles` output, or a case-insensitive
substring of a URL/title of any tab open in that profile.
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


def _profiles(rpc):
    """Profiles = browser contexts that have page targets. Sorted for stable
    indexing within one browser run (context ids change across restarts)."""
    targets = rpc("Target.getTargets")["targetInfos"]
    by_ctx = {}
    for t in targets:
        by_ctx.setdefault(t.get("browserContextId", "?"), []).append(t)
    profiles = []
    for ctx in sorted(by_ctx):
        pages = [t for t in by_ctx[ctx] if t["type"] == "page"]
        if not pages:
            continue  # UI-only context, not a profile
        sw = next((t for t in by_ctx[ctx]
                   if t["type"] == "service_worker" and EXTID in t["url"]), None)
        profiles.append({"ctx": ctx, "pages": pages, "sw": sw})
    return profiles


def _pick(profiles, selector):
    if selector is None:
        if len(profiles) == 1:
            return profiles[0]
        raise SystemExit(
            "several profiles are running — pick one with --profile:\n"
            + _fmt_profiles(profiles))
    if selector.isdigit() and 1 <= int(selector) <= len(profiles):
        return profiles[int(selector) - 1]
    s = selector.lower()
    hits = [p for p in profiles
            if any(s in (t["url"] + " " + t.get("title", "")).lower()
                   for t in p["pages"])]
    if len(hits) != 1:
        raise SystemExit(
            f"--profile {selector!r} matched {len(hits)} profiles:\n"
            + _fmt_profiles(profiles))
    return hits[0]


def _fmt_profiles(profiles):
    out = []
    for i, p in enumerate(profiles, 1):
        ext = "extension: yes" if p["sw"] else "extension: no/asleep"
        out.append(f"  {i}. [{p['ctx'][:8]}] {ext}")
        for t in p["pages"]:
            out.append(f"       {t['url'][:70]}  {t.get('title', '')[:40]}")
    return "\n".join(out)


def _wake_sw(rpc, profile):
    """Wake the profile's sleeping extension worker via a throwaway tab."""
    page = profile["pages"][0]
    sid = rpc("Target.attachToTarget",
              {"targetId": page["targetId"], "flatten": True})["sessionId"]
    rpc("Runtime.evaluate",
        {"expression": "window.open('about:blank')", "userGesture": True}, sid)
    tmp = None
    for _ in range(15):
        time.sleep(0.3)
        tmp = next((t for t in rpc("Target.getTargets")["targetInfos"]
                    if t["type"] == "page" and t["url"] == "about:blank"
                    and t.get("browserContextId") == profile["ctx"]), None)
        if tmp:
            break
    if not tmp:
        raise SystemExit("could not open a helper tab to wake the extension")
    tsid = rpc("Target.attachToTarget",
               {"targetId": tmp["targetId"], "flatten": True})["sessionId"]
    rpc("Page.navigate",
        {"url": f"chrome-extension://{EXTID}/data/popup/popup.html"}, tsid)
    sw = None
    for _ in range(15):
        time.sleep(0.4)
        sw = next((t for t in rpc("Target.getTargets")["targetInfos"]
                   if t["type"] == "service_worker" and EXTID in t["url"]
                   and t.get("browserContextId") == profile["ctx"]), None)
        if sw:
            break
    rpc("Target.closeTarget", {"targetId": tmp["targetId"]})
    if not sw:
        raise SystemExit("extension service worker not reachable — "
                         "is Proxy Switcher installed in this profile?")
    return sw


def sw_eval(expression, selector=None):
    """Evaluate a JS expression inside the chosen profile's extension worker.

    Wrap awaited work in an async IIFE — the eval context has no top-level await.
    """
    browser_ws = _http("/json/version")["webSocketDebuggerUrl"]
    ws = websocket.create_connection(browser_ws, max_size=None, suppress_origin=True)
    try:
        rpc = _rpc_factory(ws)
        profile = _pick(_profiles(rpc), selector)
        sw = profile["sw"] or _wake_sw(rpc, profile)
        sid = rpc("Target.attachToTarget",
                  {"targetId": sw["targetId"], "flatten": True})["sessionId"]
        rpc("Runtime.enable", {}, sid)
        res = rpc("Runtime.evaluate",
                  {"expression": expression, "awaitPromise": True,
                   "returnByValue": True}, sid)
    finally:
        ws.close()
    r = res.get("result", {})
    if r.get("subtype") == "error":
        raise SystemExit("JS error: " + r.get("description", str(r)))
    return r.get("value", r)


def list_profiles():
    browser_ws = _http("/json/version")["webSocketDebuggerUrl"]
    ws = websocket.create_connection(browser_ws, max_size=None, suppress_origin=True)
    try:
        print(_fmt_profiles(_profiles(_rpc_factory(ws))))
    finally:
        ws.close()


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
    selector = None
    args = []
    i = 0
    while i < len(argv):
        if argv[i] in ("--profile", "-p"):
            if i + 1 >= len(argv):
                raise SystemExit("--profile needs a value")
            selector = argv[i + 1]
            i += 2
        else:
            args.append(argv[i])
            i += 1

    cmd = args[0] if args else "get"
    if cmd == "profiles":
        list_profiles()
    elif cmd == "get":
        print(sw_eval(GET, selector))
    elif cmd == "direct":
        print(sw_eval(DIRECT, selector))
    elif cmd == "egress":
        print(sw_eval(EGRESS, selector))
    elif cmd == "set":
        host, port = args[1], args[2]
        scheme = args[3] if len(args) > 3 else "http"
        user = args[4] if len(args) > 4 else ""
        pw = args[5] if len(args) > 5 else ""
        print(sw_eval(_set_js(host, port, scheme, user, pw), selector))
    elif cmd == "eval":
        print(sw_eval(args[1], selector))
    else:
        print(__doc__)
        raise SystemExit(2)


if __name__ == "__main__":
    main(sys.argv[1:])

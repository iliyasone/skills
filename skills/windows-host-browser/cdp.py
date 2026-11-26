#!/usr/bin/env python3
"""Drive the proxy extension in the host's debug Chrome over CDP.

The extension (loaded unpacked from C:\\chrome-debug\\proxy-extension) owns
Chrome's proxy setting. It keeps a list of proxies synced from an admin API,
shows the exit country's flag in the toolbar, and exposes select(id) and
refresh() on its service worker, which this tool calls.

Endpoint defaults to http://127.0.0.1:18800 — the fixed local port that
connect.sh (next to this file) keeps pointed at the browser. Run connect.sh
first; override with $CDP_HTTP only if you are somewhere connect.sh does not
run (see SKILL.md — the Windows-side debug port itself is not fixed).

The browser can run several Chrome profiles at once. Each profile has its own
windows, cookies, copy of the extension — and therefore its own proxy setting.
Profiles surface in CDP as distinct browserContextIds; this tool maps each to
its profile dir by reading chrome://version in a throwaway tab once per browser
run, and takes the names from Local State on the Windows host (over ssh). With one profile running, commands work as
before; with several you must pick one (see `profiles` / --profile below).

Gotchas this file already handles, learned the hard way:
  * Chrome rejects the websocket unless the Origin header is absent
    (suppress_origin=True), because the browser was started without
    --remote-allow-origins.
  * The extension is MV3, so its service worker sleeps when idle and drops out
    of the target list. Waking the right profile's worker matters: we
    window.open an about:blank tab from one of the profile's own pages, CDP-
    navigate it to the extension popup (privileged, so allowed), then close it.

Requires: pip install websocket-client  (present on dev-remote and the WSL box).

Subcommands (all but `profiles` accept --profile / -p SELECTOR):
  profiles            list every profile, the main one first: dir, name,
                      running or not, proxy extension, open tabs
  open URL            open URL as a tab in the profile (default: the main one,
                      Default «Your Chrome»); prints {targetId, profile, ...}.
                      Running profile: background tab. Not running: started
                      via the `chromeprofile` scheduled task, window in front.
  get                 print the selected proxy and the checked exit (ip, country)
  list                refresh and print the extension's proxies (* = selected)
  use ID|CC           select a listed proxy by id, or the first of country CC
  direct              back to a direct connection
  set HOST PORT [SCHEME] [USER] [PASS] [CC]   add a proxy that is not in the
                      list (kept across syncs, negative id) and select it
  egress              re-check the exit ip and country through the current route
  eval "<js expr>"    run an expression in the extension service-worker context

SELECTOR is a 1-based index from `profiles` output, a profile dir or name
(«Default», «Nolan»), or a case-insensitive substring of a URL/title of a tab
open in that profile. Proxy commands need the profile running.
"""
import base64, json, os, re, subprocess, sys, time, urllib.request
import websocket  # websocket-client

UDD = "C:\\chrome-debug"  # the debug Chrome's --user-data-dir
CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
# Ilias's own profile («Your Chrome», the first one): all his logins — Notion,
# Google, Vercel, … Other profiles only when the task names them.
MAIN = "Default"
CACHE = os.path.expanduser("~/.cache/windows-host-browser/profiles.json")

# Chrome derives an unpacked extension's id from its folder path, so this id
# holds as long as the extension stays loaded from C:\chrome-debug\proxy-extension.
EXTID = "ldobdfecjhkelpkjjmofphogdpolfkkm"
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


def _win(ps):
    """Run PowerShell on the Windows host (directly from the WSL box, through
    `ssh wsl` from dev-remote) and return its stdout."""
    ps = "$ProgressPreference='SilentlyContinue';" + ps
    cmd = ("powershell -NoProfile -NonInteractive -EncodedCommand "
           + base64.b64encode(ps.encode("utf-16-le")).decode())
    try:
        cfg = open(os.path.expanduser("~/.ssh/config")).read()
    except OSError:
        cfg = ""
    if re.search(r"(?m)^Host\s+windows\b", cfg):
        argv = ["ssh", "windows", cmd]
    else:
        argv = ["ssh", "-o", "KexAlgorithms=curve25519-sha256@libssh.org,curve25519-sha256",
                "wsl", f"ssh windows '{cmd}'"]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=90)
    if r.returncode:
        raise SystemExit("Windows host: " + (r.stderr.strip() or f"exit {r.returncode}")[-500:])
    return r.stdout


def _all_dirs():
    """{profile dir: name} from Chrome's Local State, Default first."""
    raw = _win(f"Get-Content -Raw -Encoding UTF8 '{UDD}\\Local State'")
    info = json.loads(raw)["profile"]["info_cache"]
    key = lambda d: (d != MAIN, int(re.sub(r"\D", "", d) or 0))
    return {d: info[d].get("name", d) for d in sorted(info, key=key)}


def _load_cache(browser_ws):
    """ctx → profile dir, valid for one browser run (ids change on restart)."""
    try:
        c = json.load(open(CACHE))
    except (OSError, ValueError):
        c = {}
    return c if c.get("browser") == browser_ws else {"browser": browser_ws, "ctx": {}}


def _save_cache(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(c, open(CACHE, "w"))


def _helper_tab(rpc, ctx, pages, url="about:blank"):
    """Open a background tab in context ctx; returns its targetId."""
    try:
        return rpc("Target.createTarget",
                   {"url": url, "browserContextId": ctx, "background": True})["targetId"]
    except RuntimeError:
        pass  # older Chrome refuses real profiles' ids: open it from their page
    before = {t["targetId"] for t in rpc("Target.getTargets")["targetInfos"]}
    sid = rpc("Target.attachToTarget",
              {"targetId": pages[0]["targetId"], "flatten": True})["sessionId"]
    rpc("Runtime.evaluate",
        {"expression": f"window.open({json.dumps(url)})", "userGesture": True}, sid)
    for _ in range(20):
        time.sleep(0.3)
        new = [t for t in rpc("Target.getTargets")["targetInfos"]
               if t["type"] == "page" and t["targetId"] not in before
               and t.get("browserContextId") == ctx]
        if new:
            return new[0]["targetId"]
    raise SystemExit("could not open a tab in that profile")


def _ctx_dir(rpc, ctx, pages):
    """Profile dir of a running context, read from chrome://version once."""
    tid = _helper_tab(rpc, ctx, pages)
    try:
        sid = rpc("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
        rpc("Page.navigate", {"url": "chrome://version"}, sid)
        for _ in range(20):
            time.sleep(0.3)
            v = rpc("Runtime.evaluate",
                    {"expression": "document.getElementById('profile_path')?.textContent",
                     "returnByValue": True}, sid)["result"].get("value")
            if v:
                return v.rstrip("\\").split("\\")[-1]
    finally:
        rpc("Target.closeTarget", {"targetId": tid})
    raise SystemExit("could not read chrome://version")


def _profiles(rpc):
    """Every profile of the browser, Ilias's main one first. Running ones carry
    their context id, open pages and proxy-extension worker; others ctx=None."""
    browser_ws = _http("/json/version")["webSocketDebuggerUrl"]
    cache = _load_cache(browser_ws)
    by_ctx = {}
    for t in rpc("Target.getTargets")["targetInfos"]:
        by_ctx.setdefault(t.get("browserContextId", "?"), []).append(t)
    running = {}
    for ctx, ts in by_ctx.items():
        pages = [t for t in ts if t["type"] == "page"]
        if not pages:
            continue  # UI-only context, not a profile
        if ctx not in cache["ctx"]:
            cache["ctx"][ctx] = _ctx_dir(rpc, ctx, pages)
        sw = next((t for t in ts if t["type"] == "service_worker" and EXTID in t["url"]), None)
        running[cache["ctx"][ctx]] = {"ctx": ctx, "pages": pages, "sw": sw}
    _save_cache(cache)
    dirs = _all_dirs()
    for d in running:
        dirs.setdefault(d, d)
    return [{"dir": d, "name": n, **running.get(d, {"ctx": None, "pages": [], "sw": None})}
            for d, n in dirs.items()]


def _pick(profiles, selector, need_running=True):
    """SELECTOR: index from `profiles`, profile dir or name («Default», «Nolan»),
    or a substring of a URL/title open in that profile."""
    live = [p for p in profiles if p["ctx"]]
    if selector is None:
        if len(live) == 1:
            return live[0]
        raise SystemExit("pick a profile with --profile:\n" + _fmt_profiles(profiles))
    s = selector.lower()
    if selector.isdigit() and 1 <= int(selector) <= len(profiles):
        hits = [profiles[int(selector) - 1]]
    else:
        hits = [p for p in profiles if s in (p["dir"].lower(), p["name"].lower())]
        hits = hits or [p for p in live
                        if any(s in (t["url"] + " " + t.get("title", "")).lower()
                               for t in p["pages"])]
    if len(hits) != 1:
        raise SystemExit(f"--profile {selector!r} matched {len(hits)} profiles:\n"
                         + _fmt_profiles(profiles))
    if need_running and not hits[0]["ctx"]:
        raise SystemExit(f"profile {hits[0]['name']!r} is not running — "
                         f"`cdp.py open URL -p {hits[0]['dir']!r}` starts it")
    return hits[0]


def _fmt_profiles(profiles):
    out = []
    for i, p in enumerate(profiles, 1):
        main = "  ← MAIN: Ilias's own logins (Notion, Google, Vercel…)" if p["dir"] == MAIN else ""
        out.append(f"  {i}. {p['dir']} «{p['name']}»{main}")
        if not p["ctx"]:
            out.append("       not running")
            continue
        ext = "yes" if p["sw"] else "no/asleep"
        out.append(f"       running [{p['ctx'][:8]}], proxy extension: {ext}")
        for t in p["pages"]:
            out.append(f"       {t['url'][:70]}  {t.get('title', '')[:40]}")
    return "\n".join(out)


def open_url(url, selector=None):
    """Open url as a tab in a profile (default: MAIN) and print the tab.
    A running profile gets a background tab; one that isn't running is started
    through the `chromeprofile` scheduled task (its window comes to the front)."""
    browser_ws = _http("/json/version")["webSocketDebuggerUrl"]
    ws = websocket.create_connection(browser_ws, max_size=None, suppress_origin=True)
    try:
        rpc = _rpc_factory(ws)
        p = _pick(_profiles(rpc), selector or MAIN, need_running=False)
        if p["ctx"]:
            tid = _helper_tab(rpc, p["ctx"], p["pages"], url)
            how = "background tab"
        else:
            before = {t["targetId"] for t in rpc("Target.getTargets")["targetInfos"]}
            q = url.replace("'", "''").replace('"', "%22")
            _win(f"""
$a = New-ScheduledTaskAction -Execute '{CHROME}' -Argument '--user-data-dir={UDD} --profile-directory="{p['dir']}" "{q}"'
if (Get-ScheduledTask -TaskName chromeprofile -EA 0) {{ Set-ScheduledTask -TaskName chromeprofile -Action $a | Out-Null }}
else {{ Register-ScheduledTask -TaskName chromeprofile -Action $a -Settings (New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries) -Principal (New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive) | Out-Null }}
Start-ScheduledTask -TaskName chromeprofile""")
            tid = None
            for _ in range(40):
                time.sleep(0.5)
                new = [t for t in rpc("Target.getTargets")["targetInfos"]
                       if t["type"] == "page" and t["targetId"] not in before]
                if new:
                    tid = new[0]["targetId"]
                    cache = _load_cache(browser_ws)
                    cache["ctx"][new[0]["browserContextId"]] = p["dir"]
                    _save_cache(cache)
                    break
            if not tid:
                raise SystemExit("the profile did not open a tab (is Ilias logged on to Windows?)")
            how = "new window, in front"
    finally:
        ws.close()
    print(json.dumps({"targetId": tid, "profile": p["dir"], "name": p["name"], "opened": how,
                      "webSocketDebuggerUrl": BASE.replace("http", "ws", 1) + "/devtools/page/" + tid},
                     ensure_ascii=False))


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
        {"url": f"chrome-extension://{EXTID}/popup.html"}, tsid)
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
                         "is the proxy extension installed in this profile?")
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


# Every command that switches ends by printing the extension's exit check:
# {"ip", "country"} or {"error"}.
EXIT = "JSON.stringify((await chrome.storage.local.get('exit')).exit)"
GET = ("(async()=>{const s=await chrome.storage.local.get();"
       "const p=(s.proxies||[]).find(x=>x.id===s.selectedId);"
       "return JSON.stringify({proxy:p?[p.id,p.country_code,p.server+':'+p.port].join(' '):'direct',"
       "exit:s.exit,syncError:s.syncError||undefined,"
       "control:(await chrome.proxy.settings.get({})).levelOfControl})})()")
LIST = ("(async()=>{await refresh();const s=await chrome.storage.local.get();"
        "return (s.proxies||[]).map(p=>[p.id===s.selectedId?'*':' ',p.id,p.country_code,"
        "p.scheme+'://'+p.server+':'+p.port].join(' ')).join('\\n')||s.syncError||'no proxies'})()")
DIRECT = "(async()=>{await select(null);return " + EXIT + "})()"
EGRESS = ("(async()=>{const s=await chrome.storage.local.get('selectedId');"
          "await select(s.selectedId??null,false);return " + EXIT + "})()")


def _use_js(choice):
    """A number selects that proxy id; two letters select the country's
    proxy with the lowest id."""
    match = (f"p.id==={int(choice)}" if choice.lstrip("-").isdigit()
             else f"p.country_code==={json.dumps(choice.lower())}")
    return ("(async()=>{await refresh();const s=await chrome.storage.local.get('proxies');"
            f"const p=(s.proxies||[]).filter(p=>{match}).sort((a,b)=>a.id-b.id)[0];"
            f"if(!p)throw new Error('no proxy matches '+{json.dumps(choice)});"
            "await select(p.id);return " + EXIT + "})()")


def _set_js(host, port, scheme, user, pw, country):
    entry = {"scheme": scheme, "server": host, "port": int(port),
             "username": user or None, "password": pw or None,
             "country_code": country.lower() or None, "archived_at": None}
    return ("(async()=>{const l=(await chrome.storage.local.get('proxies')).proxies||[];"
            f"const e={json.dumps(entry)};"
            "const old=l.find(p=>p.id<0&&p.server===e.server&&p.port===e.port);"
            "e.id=old?old.id:Math.min(0,...l.map(p=>p.id))-1;"
            "await chrome.storage.local.set({proxies:l.filter(p=>p!==old).concat([e])});"
            "await select(e.id);return " + EXIT + "})()")


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
    elif cmd == "open":
        open_url(args[1], selector)
    elif cmd == "get":
        print(sw_eval(GET, selector))
    elif cmd == "list":
        print(sw_eval(LIST, selector))
    elif cmd == "use":
        print(sw_eval(_use_js(args[1]), selector))
    elif cmd == "direct":
        print(sw_eval(DIRECT, selector))
    elif cmd == "egress":
        print(sw_eval(EGRESS, selector))
    elif cmd == "set":
        host, port = args[1], args[2]
        scheme, user, pw, country = (args[3:] + ["http", "", "", ""][len(args[3:]):])[:4]
        print(sw_eval(_set_js(host, port, scheme, user, pw, country), selector))
    elif cmd == "eval":
        print(sw_eval(args[1], selector))
    else:
        print(__doc__)
        raise SystemExit(2)


if __name__ == "__main__":
    main(sys.argv[1:])

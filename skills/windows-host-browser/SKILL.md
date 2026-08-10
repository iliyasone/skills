---
name: windows-host-browser
description: >-
  Drive the real Chrome on Iliyas's Windows PC over CDP — the same browser he
  uses himself, signed in to his accounts (Vercel, Google Cloud, Heroku, and
  others). Use it to fetch data that sits behind his logins, open and debug a
  page live, watch a flow together, intercept requests, or set the browser's
  proxy / exit country. Use it only when Iliyas explicitly asks for his real /
  host / Windows browser — everything it does is visible on his screen.
---

# Windows host browser

The target is a long-lived Chrome on **Iliyas's Windows PC**, started with
`--remote-debugging-port=9222 --user-data-dir="C:\chrome-debug"`. It is not a
headless sandbox: it is a real browser Iliyas also uses, with his live
sessions (Vercel, Google Cloud, Heroku, and more), rendered on his physical
screen.

Two consequences drive everything below:

- **Power** — anything behind his logins is reachable without asking for
  credentials, and a page can be debugged exactly as he sees it: open tabs,
  run JS in them, watch network traffic, route through a chosen proxy.
- **Restraint** — he sees every window you open and shares every setting you
  change. Use this browser only on his explicit request; when he names the
  browser in the request, that settles it. Tell him when you open something
  visible, close tabs you opened unless he wants them kept, and undo
  browser-wide changes (proxy!) when done. For data you could equally get by
  asking him or via an authed CLI, prefer that.

## Step 0 — preflight: is there a path to the browser?

The debug port is exposed only on the Windows host's own `127.0.0.1:9222`.
The home machine (the WSL box `iliyasone`) and `dev-remote` are both on
Iliyas's **Tailscale** tailnet, which is the stable path between them:

- **On `dev-remote`**: `ssh wsl` reaches the WSL shell over the tailnet
  (`100.93.231.101`, stable across home-IP changes). The browser port isn't
  on the tailnet directly, so bring it to local `127.0.0.1:9222` with an
  on-demand forward:

  ```bash
  pgrep -f 'ssh -N .*-L 9222:127.0.0.1:9222 wsl' >/dev/null \
    || nohup ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
             -L 9222:127.0.0.1:9222 wsl >/dev/null 2>&1 &
  ```

- **On the WSL box `iliyasone`** (mirrored networking shares the host
  loopback): `http://127.0.0.1:9222` works directly, and `ssh windows`
  reaches the host shell.

Then run the check:

```bash
curl -s --max-time 4 "${CDP_HTTP:-http://127.0.0.1:9222}/json/version" || echo NO_CDP
```

A Chrome version → go to Step 1. `NO_CDP` → three possible causes, cheapest
first: the local forward isn't up (`pgrep` above finds nothing — re-run it),
the home PC / WSL box is offline (`tailscale status` — if `iliyasone` is
offline, the machine is off; nothing to fix from here), or the debug Chrome
isn't running (see "Launching"). Do not fabricate a path.

## Step 1 — connect

One gotcha applies to every websocket connection, not just the helper: Chrome
was started without `--remote-allow-origins`, so **open CDP websockets with
no Origin header** or the connection is rejected.

From there it is plain CDP against `${CDP_HTTP:-http://127.0.0.1:9222}`:

- `GET /json/list` — tabs and their websocket URLs; attach with any CDP
  client to evaluate JS, capture screenshots, or watch network events
  (`Network.enable` + `Network.requestWillBeSent` / `responseReceived`).
- `PUT /json/new?url=…` — open a page (a visible tab on Iliyas's screen —
  say so when you do it).

## Proxy control — `cdp.py`

Chrome carries the **Proxy Switcher** extension
(`iejkjpdckomcjdhmkemlfdapjodcpgih`), which owns the browser-wide proxy
setting. `cdp.py` (next to this file) drives it, hiding a second gotcha: the
extension is MV3, its service worker sleeps and drops out of `/json`;
`cdp.py` wakes it by opening the extension popup as a target, then attaches.

```bash
python3 cdp.py get                  # current browser-wide proxy setting
python3 cdp.py set 82.38.65.142 41196 http proxyuser 'PASSWORD'   # apply
python3 cdp.py egress               # prove the exit IP/country through it
python3 cdp.py direct               # revert to a direct connection
```

`set` calls `chrome.proxy.settings.set` browser-wide (exactly what the
extension popup does) and, when a username is given, writes the extension's
`auth-username`/`auth-password` storage and re-arms its `onAuthRequired`
handler so authenticated proxies don't pop a dialog. If you tunneled to a
non-default local port, export `CDP_HTTP=http://127.0.0.1:<port>`.

**Always `direct` when done** — a proxy left on changes Iliyas's own
browsing too.

### Where proxies come from

The reverse-api project owns a `proxy` table (Postgres, Heroku app
`pinc000`): columns `scheme, server, port, username, password, country_code`.
HTTP proxies with user/pass auth, selected by `country_code` (ISO-3166
alpha-2). Query it rather than hardcoding credentials:

```bash
# on dev-remote, heroku CLI is authed to app pinc000
heroku pg:psql -a pinc000 -c \
  "select scheme,server,port,username,password,country_code from proxy where country_code='nl';"
```

There is no rotation — a proxy is sticky per account — so for browser use
pick any row for the country you want.

## Launching the debug Chrome

If Step 0 says `NO_CDP` but `ssh wsl` works, start Chrome via the scheduled
task — never over plain SSH, which lands Chrome in the invisible session 0:

```bash
# from the WSL box:
ssh windows 'schtasks /run /tn chromedebug'
# from dev-remote (hop through WSL):
ssh wsl 'ssh windows "schtasks /run /tn chromedebug"'
# then poll: curl -s http://127.0.0.1:9222/json/version
```

This opens a visible window on Iliyas's screen — say so when you do it.

## How the access is wired (and repairing it)

Both machines are nodes on Iliyas's Tailscale tailnet:

- `iliyasone` = `100.93.231.101` — the WSL box on the home PC. Tailscale runs
  *inside WSL*, not on Windows; the Windows host is reached *through* WSL
  (`ssh windows`, and `127.0.0.1:9222` via WSL's mirrored networking).
- `dev-remote` = `100.105.176.17`. Its `~/.ssh/config` has `Host wsl` →
  `100.93.231.101`, and its root key is in the WSL box's `authorized_keys`,
  so `ssh wsl` works over the tailnet.

Tailnet addresses survive home-IP changes, so nothing needs reconfiguring
when Iliyas's network moves. When Step 0 says `NO_CDP`:

- **Far end offline** (PC asleep / WSL not up): `tailscale status` shows
  `iliyasone` offline. Nothing to fix from dev-remote.
- **Chrome not running** but `ssh wsl` works: see "Launching".
- **Forward not up**: re-run the on-demand forward from Step 0.

Only one home node is on the tailnet today (`iliyasone`). If Iliyas later
works from a different machine, it joins as a separate node with its own
name/IP — repoint `Host wsl` (or add `Host wsl-<name>`) at it. Do not invent
non-tailnet routes.

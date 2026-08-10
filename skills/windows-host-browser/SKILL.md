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
`--remote-debugging-port=<PORT> --user-data-dir="C:\chrome-debug"`. It is not a
headless sandbox: it is a real browser Iliyas also uses, with his live
sessions (Vercel, Google Cloud, and more), rendered on his physical screen.

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

## The port is not fixed — discover it

9222 was the original debug port, but Windows WinNAT can reserve it out from
under Chrome (see "Port gotcha"), so the launch may use any port. **Never
hardcode the port** — read it from the scheduled task, which is the source of
truth:

```bash
PORT=$(ssh wsl 'ssh windows "schtasks /query /tn chromedebug /xml"' \
        | grep -aoE 'remote-debugging-port=[0-9]+' | grep -oE '[0-9]+')
export CDP_HTTP="http://127.0.0.1:$PORT"   # cdp.py and every check below read this
```

As of 2026-08-10 the port is **9223** (9222 is WinNAT-blocked). Everything
below uses `$PORT` / `$CDP_HTTP`; substitute the discovered value.

## Step 0 — preflight: is there a path to the browser?

The debug port lives only on the Windows host's own `127.0.0.1:$PORT`. The home
machine (the WSL box `iliyasone`) and `dev-remote` are both on Iliyas's
**Tailscale** tailnet, the stable path between them. Reaching the port is a
two-hop tunnel — the WSL box cannot see the Windows loopback directly, so it
must tunnel to the host too:

- **On the WSL box `iliyasone`**: bring the host port onto WSL's loopback:

  ```bash
  pgrep -f "ssh .*-L $PORT:127.0.0.1:$PORT windows" >/dev/null \
    || ssh -f -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
           -L "$PORT:127.0.0.1:$PORT" windows
  ```

  (`ssh windows "curl 127.0.0.1:$PORT/json/version"` also works without the
  tunnel, for one-off checks.)

- **On `dev-remote`**: `ssh wsl` reaches the WSL shell over the tailnet
  (`100.93.231.101`). Chain a second forward on top of the WSL→host one above:

  ```bash
  pgrep -f "ssh -N .*-L $PORT:127.0.0.1:$PORT wsl" >/dev/null \
    || setsid ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
             -L "$PORT:127.0.0.1:$PORT" wsl >/dev/null 2>&1 < /dev/null &
  ```

Then run the check:

```bash
curl -s --max-time 4 "$CDP_HTTP/json/version" || echo NO_CDP
```

A Chrome version → go to Step 1. `NO_CDP` → causes, cheapest first: a tunnel
isn't up (re-run the forwards above); the port was stolen by WinNAT even though
Chrome is running (see "Port gotcha"); the home PC / WSL box is offline
(`tailscale status` — if `iliyasone` is offline, the machine is off, nothing to
fix from here); or the debug Chrome isn't running (see "Launching"). Do not
fabricate a path.

## Step 1 — connect

One gotcha applies to every websocket connection, not just the helper: Chrome
was started without `--remote-allow-origins`, so **open CDP websockets with
no Origin header** or the connection is rejected.

From there it is plain CDP against `$CDP_HTTP`:

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
It reads the endpoint from `$CDP_HTTP` (export it as shown above).

```bash
python3 cdp.py get                  # current browser-wide proxy setting
python3 cdp.py set 82.38.65.142 41196 http proxyuser 'PASSWORD'   # apply
python3 cdp.py egress               # prove the exit IP/country through it
python3 cdp.py direct               # revert to a direct connection
```

`set` calls `chrome.proxy.settings.set` browser-wide (exactly what the
extension popup does) and, when a username is given, writes the extension's
`auth-username`/`auth-password` storage and re-arms its `onAuthRequired`
handler so authenticated proxies don't pop a dialog.

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
task — never over plain SSH, which lands Chrome in the invisible session 0
where it exits without ever binding the port:

```bash
# from the WSL box:
ssh windows 'schtasks /run /tn chromedebug'
# from dev-remote (hop through WSL):
ssh wsl 'ssh windows "schtasks /run /tn chromedebug"'
# then poll: curl -s "$CDP_HTTP/json/version"
```

This opens a visible window on Iliyas's screen — say so when you do it. The
task runs as `LogonType=InteractiveToken` (General tab: "Run only when user is
logged on"), so it launches into his visible session and stores no password.

## Port gotcha — WinNAT can steal the debug port

**Symptom:** the debug Chrome is running and browses fine, but
`$CDP_HTTP/json/version` refuses the connection and no DevTools server exists.
Launched with `--enable-logging --v=1`, `C:\chrome-debug\chrome_debug.log`
shows `bind() ... Only one usage of each socket address ... (0x2740)` then
`Cannot start http server for devtools`.

**Cause:** Hyper-V/WSL2 **WinNAT/HNS** reserves blocks of TCP ports; after a
WSL or host restart a block can include the debug port. It becomes unbindable
even though `netstat` shows nothing on it AND it is absent from
`netsh int ipv4 show excludedportrange`. "Worked yesterday, broken today" =
the reserved block moved onto the port. Custom user-data-dir, policies, and
session 0 are red herrings here — confirm with the log line above.

**Recovery (no admin, no service restart):**

1. Find a free port — try to bind candidates and pick the first that succeeds:

   ```powershell
   foreach ($p in 9223,9250,9333,9555,18222) {
     try { $l=[System.Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,$p)
           $l.Start(); "OK $p"; $l.Stop() } catch { "FAIL $p" } }
   ```

2. Repoint the task to that port. Use PowerShell `Set-ScheduledTask`, **not**
   `schtasks /change` — the latter wrongly prompts for a Windows password even
   though the task stores none:

   ```powershell
   $a = New-ScheduledTaskAction -Execute 'C:\Program Files\Google\Chrome\Application\chrome.exe' `
        -Argument '--remote-debugging-port=<PORT> --user-data-dir=C:\chrome-debug'
   Set-ScheduledTask -TaskName 'chromedebug' -Action $a
   ```

   Quoting through `ssh wsl 'ssh windows "..."'` is brittle; run PowerShell via
   `-EncodedCommand <base64-UTF16LE>` to avoid it.

3. Kill the old instances, relaunch, verify:

   ```powershell
   Get-CimInstance Win32_Process -Filter "name='chrome.exe'" |
     ? { $_.CommandLine -like '*chrome-debug*' } | % { Stop-Process $_.ProcessId -Force }
   schtasks /run /tn chromedebug
   (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:<PORT>/json/version).Content
   ```

4. Re-run the Step 0 tunnels with the new `$PORT`.

**Durable fix (optional, needs admin):** reserve the port so WinNAT won't grab
it — `net stop winnat; netsh int ipv4 add excludedportrange protocol=tcp
startport=<PORT> numberofports=1 store=persistent; net start winnat`. Stopping
winnat briefly drops WSL2/Hyper-V NAT, which can blip an SSH path that runs
through it — do it only when a short interruption is safe.

## How the access is wired (and repairing it)

Both machines are nodes on Iliyas's Tailscale tailnet:

- `iliyasone` = `100.93.231.101` — the WSL box on the home PC. Tailscale runs
  *inside WSL*, not on Windows; the Windows host is reached *through* WSL via
  `ssh windows` (→ `127.0.0.1:2222`, key `~/.ssh/id_win`). The Windows
  loopback is **not** shared into WSL, so reaching the debug port from WSL
  needs the `ssh -L ... windows` tunnel from Step 0.
- `dev-remote` = `100.105.176.17`. Its `~/.ssh/config` has `Host wsl` →
  `100.93.231.101`, and its root key is in the WSL box's `authorized_keys`,
  so `ssh wsl` works over the tailnet.

Tailnet addresses survive home-IP changes, so nothing needs reconfiguring
when Iliyas's network moves. When Step 0 says `NO_CDP`:

- **Far end offline** (PC asleep / WSL not up): `tailscale status` shows
  `iliyasone` offline. Nothing to fix from dev-remote.
- **Chrome not running** but `ssh wsl` works: see "Launching".
- **Port stolen by WinNAT**: see "Port gotcha".
- **Tunnel not up**: re-run the forwards from Step 0.

Only one home node is on the tailnet today (`iliyasone`). If Iliyas later
works from a different machine, it joins as a separate node with its own
name/IP — repoint `Host wsl` (or add `Host wsl-<name>`) at it. Do not invent
non-tailnet routes.

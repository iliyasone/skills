#!/usr/bin/env bash
# Bring the Windows host Chrome's CDP endpoint onto a FIXED local port.
#
# Run on dev-remote. Idempotent — safe to run any time, from cron, or at the
# start of any session. On success prints the endpoint and exits 0:
#
#   CDP_HTTP=http://127.0.0.1:18800
#
# The Windows-side debug port moves (WinNAT steals it — see SKILL.md), so this
# script rediscovers it from the `chromedebug` scheduled task every time and
# rebuilds both SSH hops (dev-remote -> wsl -> windows) to land on the fixed
# local port. Agents and tools should only ever use $LOCAL below.
set -u

LOCAL=18800
CDP="http://127.0.0.1:$LOCAL"
# KEX pin works around the tailnet MTU blackhole (see SKILL.md).
SSH_OPTS=(-o ConnectTimeout=8
          -o KexAlgorithms=curve25519-sha256@libssh.org,curve25519-sha256
          -o ServerAliveInterval=30 -o ServerAliveCountMax=3)

alive() { curl -s --max-time 3 "$CDP/json/version" | grep -q '"Browser"'; }

# Fast path: everything already up.
if alive; then echo "CDP_HTTP=$CDP"; exit 0; fi

# The tailnet MTU fix does not survive a dev-remote reboot; re-apply.
MTU=$(cat /sys/class/net/tailscale0/mtu 2>/dev/null || echo 0)
[ "$MTU" -gt 1200 ] && ip link set dev tailscale0 mtu 1200

# 1. Discover the current debug port from the scheduled task (source of truth).
PORT=$(ssh "${SSH_OPTS[@]}" wsl 'ssh windows "schtasks /query /tn chromedebug /xml"' 2>/dev/null \
       | grep -aoE 'remote-debugging-port=[0-9]+' | grep -oE '[0-9]+')
if [ -z "${PORT:-}" ]; then
  echo "FAIL: cannot reach wsl/windows (is 'iliyasone' online? tailscale status)" >&2
  exit 1
fi

# 2. Make sure Chrome's CDP answers on the host; if not, (re)launch via the task.
cdp_on_host() {
  ssh "${SSH_OPTS[@]}" wsl "ssh windows \"curl -s --max-time 4 127.0.0.1:$PORT/json/version\"" 2>/dev/null \
    | grep -q '"Browser"'
}
if ! cdp_on_host; then
  ssh "${SSH_OPTS[@]}" wsl "ssh windows 'schtasks /run /tn chromedebug'" >/dev/null 2>&1
  for _ in 1 2 3 4 5 6; do sleep 3; cdp_on_host && break; done
  if ! cdp_on_host; then
    echo "FAIL: Chrome not answering on 127.0.0.1:$PORT on the host after relaunch." >&2
    echo "      Port gotcha or Death gotcha — see SKILL.md troubleshooting." >&2
    exit 1
  fi
fi

# 3. WSL hop: fixed $LOCAL on the WSL loopback -> host's current $PORT.
#    Kill any stale forward on $LOCAL first (it may point at an old port).
#    The [-]L bracket regex keeps pkill from matching the remote bash wrapper
#    whose own command line contains the pattern.
ssh "${SSH_OPTS[@]}" wsl "pkill -f -- '[-]L 127.0.0.1:$LOCAL:'" 2>/dev/null
sleep 0.5
ssh "${SSH_OPTS[@]}" wsl \
  "ssh -f -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
       -L 127.0.0.1:$LOCAL:127.0.0.1:$PORT windows" \
  || { echo "FAIL: could not open the WSL->windows tunnel" >&2; exit 1; }

# 4. Local hop: fixed $LOCAL here -> fixed $LOCAL on WSL.
pkill -f -- "[-]L 127.0.0.1:$LOCAL:127.0.0.1:$LOCAL wsl" 2>/dev/null; sleep 0.3
ssh -f -N -o ExitOnForwardFailure=yes "${SSH_OPTS[@]}" \
    -L "127.0.0.1:$LOCAL:127.0.0.1:$LOCAL" wsl \
  || { echo "FAIL: could not open the dev-remote->wsl tunnel" >&2; exit 1; }

if alive; then
  echo "CDP_HTTP=$CDP"
else
  echo "FAIL: tunnels are up but $CDP/json/version does not answer" >&2
  exit 1
fi

#!/usr/bin/env python3
"""Voice an agent reply in T3 Code.

Reads the reply text from stdin (or, with --last, the previous reply from this
Claude Code session's transcript), reads it aloud and prints a small HTML player
to stdout for the html_render tool.

Voices, in order of preference:
1. A ChatGPT Read Aloud voice from the local arbor-tts service
   (github.com/iliyasone/arbor-chatgpt-voice). When it fails, the script asks a
   T3 webhook (URL in ~/.config/t3-voice/repair-webhook) to start a thread that
   repairs it, at most once per REPAIR_COOLDOWN.
2. The free Google Translate voice.
3. Paid OpenAI gpt-audio-1.5, only when both of the above fail.

The audio is stored as an MP3 behind an 8-byte PNG signature with a .png name:
html_render only inlines local files that look like images, and the player
strips the signature again before playback.
"""

import argparse
import base64
import concurrent.futures
import glob
import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ENV_FILE = os.path.expanduser("~/.config/openai-voice/env")
REPAIR_WEBHOOK_FILE = os.path.expanduser("~/.config/t3-voice/repair-webhook")
REPAIR_COOLDOWN = 3 * 3600  # one repair thread at a time is enough; it may take a while
OUT_DIR = os.path.expanduser("~/.cache/t3-voice")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ARBOR_URL = os.environ.get("ARBOR_TTS_URL", "http://127.0.0.1:8811/v1/audio/speech")
GOOGLE_TTS_URL = "https://translate.google.com/translate_tts"
GOOGLE_MAX_CHARS = 190  # translate_tts rejects requests over ~200 characters
GOOGLE_TEMPO = 1.25  # Ilias listens to the Google voice sped up; ffmpeg applies it (translate_tts ignores ttsspeed)
PAID_MODEL = "gpt-audio-1.5"
MALE_VOICES = {"arbor", "spruce", "cove", "ember"}  # ChatGPT voices that get the male paid voice
READER_PROMPT = (
    "You are a voice actor. Read the user's text aloud exactly as written, word for word, "
    "in its language. Do not add, drop, translate or rephrase anything; no greeting, no comments. "
    "Natural, warm, lively conversational delivery, like a smart friend explaining something."
)


def api_key():
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    with open(ENV_FILE) as f:
        for line in f:
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit(f"OPENAI_API_KEY not found in env or {ENV_FILE}")


def last_reply():
    """Final text of the previous completed turn in this Claude Code session."""
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session:
        sys.exit("--last needs CLAUDE_CODE_SESSION_ID (Claude Code only); pipe the text instead")
    paths = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{session}.jsonl"))
    if not paths:
        sys.exit(f"transcript for session {session} not found")
    turns, current = [], []
    for line in open(paths[0]):
        entry = json.loads(line)
        if entry.get("isSidechain"):
            continue
        content = (entry.get("message") or {}).get("content")
        if entry.get("type") == "user":
            is_prompt = isinstance(content, str) or (
                isinstance(content, list) and any(b.get("type") == "text" for b in content)
            )
            if is_prompt and not entry.get("isMeta"):
                turns.append(current)
                current = []
        elif entry.get("type") == "assistant" and isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_use":
                    current = []  # only text after the last tool call is the final reply
                elif block.get("type") == "text" and block["text"].strip():
                    current.append(block["text"])
    turns.append(current)
    # The last turn is the one asking to speak; the reply before it is the target.
    for texts in reversed(turns[:-1]):
        if texts:
            return "\n\n".join(texts)
    sys.exit("no previous reply found in the transcript")


PATH = re.compile(r"(?<![\w/])[~.]{0,2}/?(?:[\w.@+-]+/)+[\w.@+-]*(?::\d+(?:-\d+)?)?")


def is_path(token):
    """File paths are noise when heard: /abs, ~/x, ./x, a/b/c, or dir/file.ext."""
    return bool(
        re.match(r"[~./]", token)
        or token.count("/") >= 2
        or re.search(r"\.\w{1,5}(?::\d+(?:-\d+)?)?$", token)
    )


def drop_path(match):
    token = match.group(0)
    core = token.rstrip(".")  # a path can end a sentence; keep the full stop
    return " " + token[len(core):] if is_path(core) else token


def speakable(md):
    """Strip Markdown markup, code blocks, URLs and file paths; other words stay untouched."""
    text = re.sub(r"```.*?```", " ", md, flags=re.S)
    text = re.sub(r"<[^>\n]+>", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = PATH.sub(drop_path, text)
    lines = []
    for line in text.splitlines():
        if re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", line) and "-" in line:
            continue  # table separator row
        line = re.sub(r"^\s*(#{1,6}|>+|[-*+])\s+", "", line)
        line = re.sub(r"\s*\|\s*", ", ", line.strip().strip("|").strip())
        line = re.sub(r"(\*\*|__|\*|`|~~)", "", line)
        # Tidy punctuation left behind by removed paths: "файл: , скрипт: ." -> "файл, скрипт."
        line = re.sub(r"\((?:\s*(?:см|see)\.?)?\s*\)", " ", line)
        line = re.sub(r"\s+([,.;:!?])", r"\1", line)
        line = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", line)
        line = re.sub(r"[,;:]+(?=\s*[.!?]|$)", "", line)
        line = re.sub(r"^[\s,.;:—–-]+", "", line).strip()
        if re.search(r"\w", line):
            lines.append(line if re.search(r"[.!?…:;,]$", line) else line + ".")
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def post(key, url, body):
    request = urllib.request.Request(
        url,
        json.dumps(body).encode(),
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            raise SystemExit(f"OpenAI {body['model']} failed: {error.code} {error.read().decode()[:500]}")
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 2:
                raise SystemExit(f"OpenAI {body['model']} unreachable: {error}")
            time.sleep(0.5)


def chatgpt(text, voice):
    """ChatGPT Read Aloud voice from the local arbor-tts service."""
    request = urllib.request.Request(
        ARBOR_URL, json.dumps({"input": text, "voice": voice}).encode(), {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            audio = response.read()
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError) as error:
        raise SystemExit(f"arbor-tts failed: {error}")
    if not audio:
        raise SystemExit("arbor-tts returned no audio")
    return audio


def google_pieces(text):
    """Split at sentence ends, then at commas and spaces, into pieces Google Translate accepts."""
    pieces, current = [], ""
    for part in re.split(r"(?<=[.!?…,;:])\s+", text):
        while len(part) > GOOGLE_MAX_CHARS:
            cut = part.rfind(" ", 0, GOOGLE_MAX_CHARS)
            cut = cut if cut > 0 else GOOGLE_MAX_CHARS
            if current:
                pieces.append(current)
                current = ""
            pieces.append(part[:cut])
            part = part[cut:].strip()
        if current and len(current) + len(part) + 1 > GOOGLE_MAX_CHARS:
            pieces.append(current)
            current = ""
        current = f"{current} {part}".strip()
    return pieces + [current] if current else pieces


def google(text):
    """Free Google Translate voice: the fallback when the ChatGPT voice fails."""
    def fetch(piece):
        lang = "ru" if re.search(r"[а-яё]", piece, re.I) else "en"
        query = urllib.parse.urlencode({"ie": "UTF-8", "client": "tw-ob", "tl": lang, "q": piece})
        request = urllib.request.Request(f"{GOOGLE_TTS_URL}?{query}", headers={"User-Agent": "Mozilla/5.0"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.read()
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == 2:
                    raise SystemExit(f"Google Translate TTS failed: {error}")
                time.sleep(0.5)

    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        audio = b"".join(pool.map(fetch, google_pieces(text)))
    try:
        slowed = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-filter:a", f"atempo={GOOGLE_TEMPO}", "-f", "mp3", "pipe:1"],
            input=audio, capture_output=True, check=True, timeout=120,
        ).stdout
        return slowed or audio
    except (OSError, subprocess.SubprocessError):
        return audio


def paid(text, voice):
    """OpenAI gpt-audio-1.5: the last resort, it costs money."""
    reply = json.loads(post(api_key(), "https://api.openai.com/v1/chat/completions", {
        "model": PAID_MODEL,
        "modalities": ["text", "audio"],
        "audio": {"voice": "cedar" if voice in MALE_VOICES else "marin", "format": "mp3"},
        "messages": [
            {"role": "system", "content": READER_PROMPT},
            {"role": "user", "content": text},
        ],
    }))
    return base64.b64decode(reply["choices"][0]["message"]["audio"]["data"])


def request_repair(error, fallback):
    """Start a T3 thread that repairs the ChatGPT voice, unless one was started recently."""
    stamp = f"{OUT_DIR}/repair-requested"
    if os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < REPAIR_COOLDOWN:
        return
    try:
        with open(REPAIR_WEBHOOK_FILE) as f:
            url = f.read().strip()
        body = {"error": error, "fallback": fallback, "time": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
        request = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
        urllib.request.urlopen(request, timeout=15).close()
    except (OSError, urllib.error.URLError, http.client.HTTPException) as e:
        print(f"t3-voice: could not request a repair: {e}", file=sys.stderr)
        return
    os.makedirs(OUT_DIR, exist_ok=True)
    open(stamp, "w").close()
    print("t3-voice: started a repair thread", file=sys.stderr)


PLAYER = (
    # Own controls instead of <audio controls>: T3's full-screen button covers the native speed menu.
    # The right 44px stay empty for that button.
    '<style>#p{display:flex;align-items:center;gap:10px;height:44px;padding-right:44px;font:13px var(--font-sans)}'
    '#p button{flex:none;height:36px;border:1px solid var(--border);border-radius:18px;background:var(--secondary);'
    'color:var(--secondary-foreground);font:inherit;cursor:pointer}#g{width:36px;font-size:15px}#r{min-width:52px}'
    '#s{flex:1;min-width:40px;accent-color:var(--primary)}#t{flex:none;color:var(--muted-foreground);font-variant-numeric:tabular-nums}'
    '</style><div id=p><button id=g>▶</button><input id=s type=range min=0 max=1 step=any value=0>'
    '<span id=t>0:00</span><button id=r>1×</button></div><audio id=a></audio><script>'
    'const S="__PATH__",b=atob(S.slice(S.indexOf(",")+1));'
    'a.src=URL.createObjectURL(new Blob([Uint8Array.from(b,c=>c.charCodeAt(0)).slice(8)],{type:"audio/mpeg"}));'
    'const R=[1,1.25,1.5,1.75,2],f=x=>(x/60|0)+":"+String(x%60|0).padStart(2,"0");'
    'let k=0;try{k=Math.max(0,R.indexOf(+localStorage.t3voiceRate))}catch(e){}'
    'const rate=()=>{a.defaultPlaybackRate=a.playbackRate=R[k];r.textContent=R[k]+"×"};rate();'
    'r.onclick=()=>{k=(k+1)%R.length;rate();try{localStorage.t3voiceRate=R[k]}catch(e){}};'
    'g.onclick=()=>a.paused?a.play():a.pause();a.onplay=()=>g.textContent="❚❚";a.onpause=()=>g.textContent="▶";'
    'a.onloadedmetadata=a.ontimeupdate=()=>{s.max=a.duration||1;s.value=a.currentTime;'
    't.textContent=f(a.currentTime)+" / "+f(a.duration||0)};s.oninput=()=>a.currentTime=+s.value;'
    # Media keys and a "play/pause" gesture reach the reply through the app's media session; at the end it
    # rewinds and stays paused, so the session (and the gesture) survives to play it again.
    'const M=navigator.mediaSession;if(M){M.setActionHandler("play",()=>a.play());M.setActionHandler("pause",()=>a.pause());'
    'M.setActionHandler("stop",()=>{a.pause();a.currentTime=0})}a.onended=()=>{a.currentTime=0;a.pause()};'
    'Date.now()-__MADE__<12e4&&a.play().catch(()=>{})</script>'
)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--last", action="store_true", help="speak the previous reply of this session")
    parser.add_argument("--voice", default="sol", help="ChatGPT voice: arbor, sol, breeze, juniper, maple, vale, ... (default: sol)")
    args = parser.parse_args()

    started = time.time()
    text = speakable(last_reply() if args.last else sys.stdin.read())
    if not text:
        sys.exit("nothing to speak")
    try:
        audio, engine = chatgpt(text, args.voice), f"chatgpt/{args.voice}"
    except SystemExit as error:
        failure = str(error)
        try:
            audio, engine = google(text), "google (fallback)"
        except SystemExit as google_error:
            failure += f"; Google Translate fallback failed too: {google_error}"
            print(f"t3-voice: {failure}; using paid OpenAI", file=sys.stderr)
            request_repair(failure, f"paid OpenAI {PAID_MODEL}")
            audio, engine = paid(text, args.voice), f"{PAID_MODEL} (fallback)"
        else:
            print(f"t3-voice: {failure}; using Google Translate", file=sys.stderr)
            request_repair(failure, "Google Translate")

    os.makedirs(OUT_DIR, exist_ok=True)
    for old in glob.glob(f"{OUT_DIR}/*.png"):
        if time.time() - os.path.getmtime(old) > 7 * 86400:
            os.remove(old)
    path = f"{OUT_DIR}/{uuid.uuid4().hex[:12]}.png"
    with open(path, "wb") as f:
        f.write(PNG_SIGNATURE + audio)

    print(PLAYER.replace("__PATH__", path).replace("__MADE__", str(int(time.time() * 1000))))
    print(
        f"t3-voice: {engine}, {len(text)} chars, {len(audio) // 1024} KiB, "
        f"synthesized in {time.time() - started:.1f}s",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

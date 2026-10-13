#!/usr/bin/env python3
"""Voice an agent reply in T3 Code.

Reads the reply text from stdin (or, with --last, the previous reply from this
Claude Code session's transcript), reads it aloud with OpenAI (gpt-audio-1.5 by default,
a tts-1 model, or ChatGPT's Arbor voice via the local arbor-tts service), and prints a small HTML player to stdout for the html_render tool.

The audio is stored as an MP3 behind an 8-byte PNG signature with a .png name:
html_render only inlines local files that look like images, and the player
strips the signature again before playback.
"""

import argparse
import base64
import concurrent.futures
import glob
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
OUT_DIR = os.path.expanduser("~/.cache/t3-voice")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
ARBOR_URL = os.environ.get("ARBOR_TTS_URL", "http://127.0.0.1:8811/v1/audio/speech")
GOOGLE_TTS_URL = "https://translate.google.com/translate_tts"
GOOGLE_MAX_CHARS = 190  # translate_tts rejects requests over ~200 characters
GOOGLE_TEMPO = 0.85  # Google's voice reads fast; ffmpeg slows it down (translate_tts ignores ttsspeed)
TTS_MAX_CHARS = 4000  # /v1/audio/speech accepts at most 4096 characters
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


def tts_pieces(text):
    """Split at sentence ends into pieces the speech endpoint accepts."""
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?…])\s+", text):
        if current and len(current) + len(sentence) + 1 > TTS_MAX_CHARS:
            pieces.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
        while len(current) > TTS_MAX_CHARS:
            pieces.append(current[:TTS_MAX_CHARS])
            current = current[TTS_MAX_CHARS:]
    return pieces + [current] if current else pieces


def arbor(text):
    """ChatGPT Read Aloud voice Arbor from arbor-tts (github.com/iliyasone/arbor-chatgpt-voice)."""
    request = urllib.request.Request(
        ARBOR_URL, json.dumps({"input": text, "voice": "arbor"}).encode(), {"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as error:
        raise SystemExit(f"arbor-tts failed: {error}")


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
    """Free Google Translate voice: the fallback when OpenAI is out of credits or down."""
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


def synthesize(key, text, model, voice):
    if model == "arbor":
        return arbor(text)
    if model.startswith("tts-"):
        body = lambda piece: {"model": model, "voice": voice, "input": piece, "response_format": "mp3"}
        pieces = tts_pieces(text)
        with concurrent.futures.ThreadPoolExecutor(len(pieces)) as pool:
            return b"".join(pool.map(lambda piece: post(key, "https://api.openai.com/v1/audio/speech", body(piece)), pieces))
    reply = json.loads(post(key, "https://api.openai.com/v1/chat/completions", {
        "model": model,
        "modalities": ["text", "audio"],
        "audio": {"voice": voice, "format": "mp3"},
        "messages": [
            {"role": "system", "content": READER_PROMPT},
            {"role": "user", "content": text},
        ],
    }))
    return base64.b64decode(reply["choices"][0]["message"]["audio"]["data"])


PLAYER = (
    '<audio id=a controls style="width:100%;height:40px"></audio><script>'
    'const S="__PATH__",b=atob(S.slice(S.indexOf(",")+1));'
    'a.src=URL.createObjectURL(new Blob([Uint8Array.from(b,c=>c.charCodeAt(0)).slice(8)],{type:"audio/mpeg"}));'
    # Media keys and a "play/pause" gesture reach the reply through the app's media session; at the end it
    # rewinds and stays paused, so the session (and the gesture) survives to play it again.
    'const M=navigator.mediaSession;if(M){M.setActionHandler("play",()=>a.play());M.setActionHandler("pause",()=>a.pause());'
    'M.setActionHandler("stop",()=>{a.pause();a.currentTime=0})}a.onended=()=>{a.currentTime=0;a.pause()};'
    'Date.now()-__MADE__<12e4&&a.play().catch(()=>{})</script>'
)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--last", action="store_true", help="speak the previous reply of this session")
    parser.add_argument("--model", default="gpt-audio-1.5", help="gpt-audio-*, tts-1*, arbor or google (default: gpt-audio-1.5)")
    parser.add_argument("--voice", default="marin", help="OpenAI voice (default: marin)")
    args = parser.parse_args()

    started = time.time()
    text = speakable(last_reply() if args.last else sys.stdin.read())
    if not text:
        sys.exit("nothing to speak")
    engine = args.model if args.model in ("arbor", "google") else f"{args.model}/{args.voice}"
    if args.model == "google":
        audio = google(text)
    else:
        try:
            audio = synthesize(None if args.model == "arbor" else api_key(), text, args.model, args.voice)
        except SystemExit as error:
            print(f"t3-voice: {error}; falling back to Google Translate", file=sys.stderr)
            audio, engine = google(text), "google (fallback)"

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

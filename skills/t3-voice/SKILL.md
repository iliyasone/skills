---
name: t3-voice
description: always enabled — use for every reply, unless the user explicitly asks not to
---

# t3-voice

Always on: voice every reply unless Ilias explicitly asks not to.

1. Write the reply as usual. Paths, URLs and code blocks are not read aloud,
   so write files as named links: `[SKILL.md](/abs/path/SKILL.md)`.
2. Pipe the exact text into the script:

   ```bash
   ~/.agents/skills/t3-voice/speak.py --voice <voice> <<'EOF'
   <the reply, verbatim>
   EOF
   ```

3. Call `html_render` with `html` = the script's stdout, `title` = `🔊`,
   `height` = `80`. Skip `html_preview`.
4. Send the same text as your final reply.

If the script fails, send the reply as text with one line saying the voice
failed.

Claude is Клауди, female: Opus `--voice sol`, Fable `--voice breeze`.
Codex is male: `--voice arbor`.

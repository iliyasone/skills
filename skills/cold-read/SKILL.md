---
name: cold-read
description: Write agent-facing files (SKILL.md, AGENTS.md, CLAUDE.md, READMEs, runbooks) so they survive a first-time reader with no shared history. Use when creating or editing any such file, or reviewing one for noise.
---

# Cold read

Every doc is eventually read by someone — human or agent — who was in none of
the conversations that produced it. Write for that reader: the current state
of the world, self-explanatory, and nothing else.

Before writing, simulate that reader: what do they need in order to act, what
can they not possibly know, which line would they have to ask about? Every
sentence that fails the simulation is rewritten to stand on its own or
deleted.

## Rules

- **Current state only.** How things used to be, what was migrated or
  abandoned, what is no longer used — invisible to the doc. A fact that
  matters only as contrast with the past doesn't matter.
- **No references that need history.** A name, codename, or decision that only
  a past conversation explains either becomes a general rule the reader can
  apply on their own, or goes. If generalizing needs a fact nobody wrote down,
  cut the line and flag it to the owner — never invent the rationale.
- **Nothing obvious.** A warning no reader would violate is noise; enforce
  mechanically instead (.gitignore, lint, CI) and stay silent.
- **No meta-commentary.** Nothing about how the doc was written or what was
  agreed along the way — state the rules themselves.
- **Explain things at the point of use.** A variable, secret, or constant is
  described where it is used — the skill, module, or config that reads it; a
  central doc says only where such things come from and how to fetch them.
- **English**, except literal data — names of external entities (databases,
  properties, pages) stay exactly as they are spelled there.

For skills specifically:

- The description is a trigger, not a manual: what the skill does and when to
  fire it. Implementation details live in the body, loaded only on use.
- Skills are never listed in AGENTS.md / CLAUDE.md — they announce themselves
  through their own descriptions.

## Verify empirically

The author cannot see their own blind spots — every line reads as obvious to
the person who already knows the story. A cold reader finds the gaps in
minutes. So test the doc the way it will actually be consumed: spawn a fresh
subagent that reads only the doc, and

1. give it scenario questions ("you're asked to do X — walk through what you
   do") and check it reaches the right actions;
2. ask what confused it, what it needed but couldn't find, and what it read
   but didn't need.

Fix what it misread, then retest the fixes with another fresh reader. If no
subagent is available, degrade to a self-check: reread the doc listing every
fact a stranger couldn't source from the doc itself.

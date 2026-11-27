---
name: daily-reflection
description: Nightly reflection on a day of work with the user in one T3 Code project — where agents let the user down, why, and which memory rule would prevent it — written into the project's memory under git. Use to set up the nightly reflection for a project ("поставь рефлексию", "set up the daily reflection"), and when a scheduled task asks you to run it.
---

# Daily reflection

Agents already write rules into memory during a chat. What no single chat sees
is the pattern across a day: the same mistake in several chats, the moment the
user got angry and why, a rule that exists but was ignored. This skill reads
the day's chats of one project as a whole and turns those patterns into memory.

The user is not always right. People correct an agent while tired or angry,
misremember, or contradict what they said last week. A reflection that writes
every angry sentence as a rule makes memory worse. Separate what the agent did
wrong from what the user said in the heat of the moment.

## Set it up for a project

1. Find the project's T3 id (`t3_project_list`) and its memory file:
   `~/.agents/memory/projects/<repo>.md`, where `<repo>` is the name of the
   repo's origin. If the project has no such file yet, give it one first with
   `~/.agents/memory/add-project.sh <repo dir>`.
2. Create a scheduled task with `schedule_task` in that project:
   `{"type": "fixed_time", "timeOfDay": "04:00"}`, title `🪞 Рефлексия <project>`,
   and this prompt (fill in the brackets):

   > Run the daily-reflection skill (section "Run") for T3 project
   > `<project id>`; memory file `<path>`; state file
   > `<memory dir>/projects/.<repo>.reflection-since`. Work silently: no voice,
   > no Telegram.

   The prompt names the skill instead of copying its steps, so every run uses
   the current version of this file.
3. Create the state file with the current UTC time
   (`date -u +%Y-%m-%dT%H:%M:%S.000Z`), so the first run covers one day.

## Run

1. Collect the day:
   `python3 <this skill's dir>/digest.py --project <id> --since-file <state file> --chunk-chars 200000 --out /tmp/reflection-<repo>.md`.
   It prints how many threads had messages from the user.
2. **Fewer than 6 threads → no reflection.** A quiet day has no pattern worth
   writing. `mv <state file>.next <state file>`, settle the thread
   (`t3_thread_organize`, action `settle`) and stop.
3. Read every chunk; with several, give each to a subagent with these
   questions and merge the answers. For each thread find:
   - where the user corrected an agent, repeated a request, or got angry —
     quote the words;
   - what the agent did and what the user wanted instead;
   - why it happened: a memory rule missing, a rule present but ignored, a
     rule worded too narrowly or lost in a shortening, a misread dictation,
     or the user changing their mind;
   - whether the user may be wrong: it was said in anger, it contradicts an
     earlier word, or the agent was right on the facts.
4. Look across threads for what repeats. One slip is noise; the same failure
   in two or more chats, or one that cost money, data or a merge the user didn't
   allow, is a pattern.
5. Write the patterns into memory, by the rules in each memory file's header:
   - a rule for this project goes into the project's memory file; one for
     every project goes into `~/.agents/memory/MEMORY.md`;
   - phrase it as what to do, with the date;
   - if the rule already exists and was ignored, make it clearer or move it
     up; don't add a second copy;
   - don't write what may be the user's mistake; leave it out;
   - when the user said they don't want some rule, don't write the opposite
     rule: just don't have one.
   - Also check the day's uncommitted edits to both files
     (`git -C ~/.agents/memory diff`) for duplicates and format.
6. `mv <state file>.next <state file>`, then
   `git -C ~/.agents/memory add -A` and
   `git -C ~/.agents/memory commit -m "reflection <dd.mm> — <what changed>"`.
   Git keeps every version, so a wrong rule can be found and reverted later.
7. Find the work the user explicitly asked to get done that is still not
   done: a request with no PR, a PR waiting for the user's "continue", a fix
   an agent promised and dropped. Check its current state (main, deploys,
   data) first; another chat may have finished it. When you are sure it is
   what the user wants and how to do it is clear, do it yourself: open the PR
   and drive it through review, merging only where the memory allows.
8. Report in this thread, in the user's language, only that unfinished work:
   what was asked and where (thread link), and the PR you opened or why you
   could not. Don't list the day's conflicts, the rules you wrote or
   questions: the rules are in the memory's git log, and the reader does not
   want a review of the day. With nothing unfinished, write one line saying
   so. Then settle the thread (`t3_thread_organize`, action `settle`).

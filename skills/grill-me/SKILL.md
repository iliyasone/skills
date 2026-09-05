---
name: grill-me
description: Interview the user about a plan, design or decision until every open choice is settled, one question at a time through the AskUserQuestion tool. Use when the user says "grill me", "grill-me", "/grill-me", asks to stress-test an idea, or wants to reach shared understanding before anything is built.
---

# Grill me

The user has a plan or a design in their head. Your job is to pull every
decision out of it, one at a time, until nothing is left silently assumed,
and only then build.

## The design tree

Treat the design as a tree: every decision branches into the decisions that
hang off it. The **frontier** is the set of decisions whose prerequisites
are already settled, so they can be asked without guessing at answers you
have not heard yet.

Every answer reshapes the tree: a settled decision pushes the frontier
outward and unblocks the questions that depended on it. Recompute the
frontier after each answer. A question whose answer depends on another still
open question belongs later, not now.

The session is done when the frontier is empty: every branch visited,
nothing left assumed.

## One question per turn, through the tool

Ask with the AskUserQuestion tool, never as prose. One question per turn:
ask, wait for the answer, recompute, ask the next. Several questions at once
leave the user not knowing where to start.

Each question:

- Two to four concrete options. Your recommended option goes first, with
  "(Recommended)" appended to its label. The user can always type their own
  answer, so do not add an "other" option.
- The option descriptions carry the trade-off, so the user can decide
  without asking back.
- Written in the language the user writes in.

Example, one AskUserQuestion call:

```
question: "Whose bookmaker account places a partner user's bets?"
header:   "Bet account"
options:
  - label: "The partner's own account (Recommended)"
    description: "Their balance is the real balance; we never hold their money. Needs their credentials stored and a second logged-in session per partner."
  - label: "Our account, partner settled by ledger"
    description: "One session, one set of credentials. We carry the float and must settle every bet ourselves."
```

## Facts are your job, decisions are the user's

Never ask the user for anything you can look up: what the code does today,
what the schema holds, what a dependency supports, what the logs show. Find
it first, from the repository, the tools you have, and the environment, and
put the fact into the question as context. When a fact needs a longer
search, run it in the background and ask the frontier questions that do not
depend on it meanwhile.

The decisions themselves belong to the user. Put each one to them and wait,
even when the answer looks obvious.

## Closing

When the frontier is empty, ask one last question that lists every decision
made, in the order they were settled, and asks whether this is the shared
understanding. Do not write code, open files for editing, or start a PR
before the user confirms that question. If they change something, reopen
that branch of the tree and grill again from there.

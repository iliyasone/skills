---
name: postplan
description: Publish a write-up as a rendered HTML page at postplan.dev and hand back the link, or read a plan supplied as a postplan.dev URL. Use for a plan, spec, findings, summary, report, comparison, or UI mocks — or when the user mentions HTML with no other context.
---

# Postplan

Chat renders markdown badly. A plan, a comparison, or a set of UI mocks is
easier to judge as a page in a browser. Write one HTML file, upload it, and
reply with nothing but the URL — the user opens it wherever they are, phone
included.

## Publish

1. Write one complete static HTML document locally.
2. `npx postplan upload <file>` — prints a draft URL and a `/raw` URL.
3. Give the user the URL. That link is the deliverable.

Re-uploading the same local file updates the existing draft, so the URL stays
stable across iterations; keep one file per document rather than starting a new
one each revision. `--new` forces a separate draft, `--description "<label>"`
sets the label shown in the dashboard. Credentials and file-to-draft mappings
live in `~/.postplan`; an API key is optional and only needed for `postplan
list` and the dashboard.

Never announce the link before the upload succeeds, and don't open a browser to
check the result unless asked.

## Write the document

Self-contained and static: semantic HTML, inline `<style>`, charset/viewport/
title, HTTPS or data-URL images. No JavaScript, event handlers, forms, iframes,
or meta refresh — uploads containing them are rejected. Nothing secret either;
these URLs are public to anyone holding them, so no tokens, private hostnames,
or local paths.

Write it like a spec, not a landing page. For UI mocks, label the options A, B,
C and lay them side by side, so the reply can be just the letters.

## Read

Given a postplan.dev URL, fetch it with the shell — strip a trailing slash,
append `/raw`, `curl --fail --silent --location`. Never a browser or web
search. Every URL returns the uploaded HTML byte for byte, so a failure is a
real network or status error worth reporting, not a reason to go searching.

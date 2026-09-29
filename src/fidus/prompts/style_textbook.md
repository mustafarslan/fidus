## Textbook style guide

Every chapter follows this skeleton. Omit a section only if it genuinely does not apply.

```markdown
---
title: <Chapter title>
fidus:
  chapter: <chapter-id>
---

# <Chapter title>

> **Learning objectives**
> - 3–5 concrete things the reader will be able to explain or do

**Prerequisites:** [<Earlier chapter>](relative/link.md), …   (or "None")

## Motivation
Why this part of the system exists and what problem it solves.

## <Core concept sections, ordered from simple to complex>
Define every term the first time you use it. Build on earlier chapters and link to them instead of
re-explaining them.

## Worked example
A walkthrough built **only** from real code excerpts copied verbatim from the source (each with a
citation line such as `api:src/auth/jwt.py`), connected by prose. Trace what the real code does,
step by step.

## How it works internally
Control flow, data flow and key design decisions. Mermaid diagrams are welcome.

## Common pitfalls
Mistakes, edge cases and operational gotchas that you can see in the code.

## Summary
A short recap tied back to the learning objectives.

## Sources
- `alias:path/to/file` — what it contains
```

Voice:
- Clear, precise explanatory prose aimed at a capable engineer who is new to this codebase.
- Teach, don't just list. Explain *why* as well as *what*.
- Keep to this chapter's scope as described in the outline summary. Material that belongs to another
  chapter gets a link instead.
- Cross-link other chapters with relative Markdown links computed from the outline paths, for
  example `[Authentication](../part-2-core/01-auth.md)`.
- Copy code blocks verbatim from the source. Never write pseudo-code that looks like real code,
  and never invent example calls. If you illustrate a call, copy an actual call site.
- **No hypothetical scenarios with invented code or output.** That rules out:
  - made-up diffs or PRs ("imagine a PR that adds a `timeout` parameter")
  - fake tool-call transcripts or log output
  - invented function signatures, parameters or return values

  Readers copy what they see, and invented examples read as fact. To explain a process, describe
  it in prose using real identifiers, and quote the real code that implements each step.
- Describe behaviour exactly as the code implements it, including edge cases. Don't describe
  what a system like this "typically" does.

Human-owned passages:
- Text between `<!-- fidus:keep -->` and `<!-- /fidus:keep -->` was written by a human. Reproduce it
  exactly, byte for byte, and in the same place.

When updating an existing chapter:
- Make the smallest correct change. Keep the structure, wording and sections that are still
  accurate.
- Do not rewrite for style.
$style_extra

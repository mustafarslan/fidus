## Your task: update chapter $number "$title" (`$chapter_id`)

Some pull requests were merged into the source repositories, and they may affect this chapter.
Bring the chapter in line with the **current** code.

- Chapter file: `$path`. Level: $level.
- Scope, from the outline: $summary
- Source globs mapped to this chapter: $sources
- Prerequisite chapters: $prerequisites

### Triggering changes
$triggers

### How to work
1. Read the triggering PRs with `get_pr` to see what changed. Diffs explain the change, but the clone
   is the truth: use `read_file` and `search_code` to confirm the current behaviour.
2. Compare that with the current chapter, shown below.
3. If the chapter is now wrong or incomplete, make the **smallest correct edit** and call
   `write_doc` with the complete updated chapter.
4. Call `done`, with a one-line reason for **every** triggering change in `trigger_reasons`: what you
   changed, or why no change was needed.
5. If you renamed or removed a concept that later chapters may reference, list it in
   `concepts_changed`.

$known_problems### Current chapter content
$current_doc

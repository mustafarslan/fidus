## Your task: write chapter $number "$title" (`$chapter_id`) from scratch

- Chapter file: `$path`. Level: $level.
- Scope, from the outline: $summary
- Source globs mapped to this chapter: $sources
- Prerequisite chapters, already written or being written, which you can read with `read_doc`:
  $prerequisites

### How to work
1. Use `read_outline` to see where this chapter sits in the book.
2. Explore the mapped sources with `list_files`, `read_file` and `search_code` until you understand
   them well enough to teach them.
3. Write the whole chapter following the style guide and call `write_doc`. Fix any validation
   problems it reports.
4. Call `done`, with `changed: true`.

Pitch the chapter at its level. **Fundamentals** chapters assume no prior knowledge of this
codebase. **Advanced** chapters can assume that the reader has read their prerequisites.

$known_problems$current_doc

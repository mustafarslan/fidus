## Your task: audit chapter $number "$title" (`$chapter_id`)

This is a periodic full audit. Re-verify every factual claim in the chapter against the **current**
source code: identifiers, signatures, configuration keys, defaults, file paths, behaviour, and code
excerpts, which must still match the source verbatim.

- Chapter file: `$path`. Level: $level.
- Scope, from the outline: $summary
- Source globs mapped to this chapter: $sources
- Prerequisite chapters: $prerequisites

### How to work
1. Read the chapter below. For each claim, check the code with `read_file` and `search_code`.
2. Fix any drift with minimal edits and call `write_doc`. Also look for important new behaviour in
   the mapped sources that the chapter should cover but doesn't.
3. Call `done`. Report each problem you found in `findings` (severity `warn` for drift you fixed,
   `error` for problems you could not fix). If the chapter is accurate, report a single `info`
   finding.
4. Ideas about the book's structure go in `structure_suggestions` only. Never restructure the book
   yourself.

### Current chapter content
$current_doc

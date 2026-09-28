## Your task: design the outline of a textbook about these repositories

Design a book, **"$book_title"**, that teaches a new engineer how this software works. It must go
from fundamentals up to advanced topics, like a computer-science textbook.

### Repository digest
$digest

### Requirements
- Organise the book into **Parts** that each contain **Chapters**. Typical parts are Foundations
  (what the system is, core concepts, architecture overview, data model), Core mechanisms (the main
  subsystems), Operations (configuration, deployment, observability) and Advanced topics
  (internals, performance, extension points). Adapt these to the codebase.
- Order chapters so that each one only depends on earlier ones.
- Every chapter needs:
  - `id`: kebab-case, stable
  - `title`
  - `path`: relative, e.g. `part-1-foundations/01-overview.md`
  - `level`: fundamentals | intermediate | advanced
  - `summary`: 1–2 sentences on the scope
  - `sources`: globs prefixed by the repo alias, e.g. `api:src/auth/**`
  - `prerequisites`: ids of earlier chapters
- Aim for roughly $target_chapters chapters in total, sized to the codebase. Give every important
  source area at least one chapter.
- Explore with `list_files`, `read_file` and `search_code` until you understand the architecture,
  then call `propose_outline` exactly once. Part ids must be kebab-case too.

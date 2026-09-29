## Your task: close the coverage gaps in the outline

The outline you proposed covers only $coverage of the source files. These files and directories
aren't matched by any chapter's `sources` globs, which means changes to them would never update
the documentation:

$uncovered

Current outline:

```json
$outline_json
```

Fix it:
- Extend existing chapters' `sources` globs where the files belong to that chapter's topic.
- Add new chapters only for genuinely separate topics. Keep chapters ordered from fundamentals to
  advanced, with prerequisites pointing to earlier chapters.
- Keep every existing chapter `id` and `path` unchanged.
- Leave out files with no documentation value (tests, fixtures, generated files) if they happen
  to be listed.

You may inspect files with `list_files` and `read_file`. Submit the complete revised outline with
`propose_outline`.

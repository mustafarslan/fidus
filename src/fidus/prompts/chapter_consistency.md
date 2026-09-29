## Your task: consistency pass on chapter $number "$title" (`$chapter_id`)

An earlier chapter that this one builds on was just updated, and these concepts changed:

$changes

Check whether this chapter still refers to the old names, definitions or behaviour. Fix **only**
those references: terminology, links and statements that now contradict the updated chapter. Keep
every other part of the chapter as it is.

If nothing needs to change, call `done` with `changed: false`. Otherwise call `write_doc`, then
`done`.

$known_problems### Current chapter content
$current_doc

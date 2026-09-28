## Your task: revise the textbook outline

The weekly audit produced these structure suggestions:

$suggestions

Here is the current outline, as JSON:

$outline_json

Return the complete revised outline by calling `propose_outline`. Rules:
- Keep existing chapter `id`s and `path`s for chapters that still exist, so links don't break.
- Only make changes that the suggestions justify.
- Prerequisites must point to earlier chapters.

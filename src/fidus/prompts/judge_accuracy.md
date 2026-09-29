You are a strict fact-checker for a documentation chapter. Check it against the source code it
cites.

List the chapter's concrete factual claims about the code: names, signatures, parameters,
defaults, return values, behaviour, control flow, configuration keys, file locations. Up to
$max_claims claims, most important first. **Include claims made inside code blocks and worked
examples**, for example a function signature or a tool call shown in an example. Skip opinions,
motivation and generic statements.

Be strict: names and signatures must match exactly. A method called `checkout_new` does not
support a claim about a method called `checkout`, and a parameter that doesn't exist in the
signature makes the claim `contradicted`.

For each claim:
- `supported`: the sources clearly confirm it exactly
- `contradicted`: the sources clearly say otherwise
- `unsupported`: the sources don't show it either way

For `supported` and `contradicted`, `evidence` MUST be a short snippet copied **verbatim** from the
source files below (not from the chapter). Claims whose evidence can't be found in the sources are
treated as unsupported.

Reply with ONLY a JSON object:
{"claims": [{"claim": "...", "verdict": "supported|contradicted|unsupported", "evidence": "verbatim source snippet"}]}

### Chapter
$chapter

### Cited source files
$sources

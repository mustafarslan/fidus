"""Render a simulation as a Markdown timeline."""

from __future__ import annotations

from fidus.sim.runner import SimResult


def render(sim: SimResult) -> str:
    mode = (
        "strict (fake model: expectations are assertions)"
        if sim.strict
        else "observational (real model: expectations are reported, not enforced)"
    )
    passed = sum(ok for d in sim.days for _, ok in d.checks)
    total = sum(len(d.checks) for d in sim.days)
    q_passed = sum(ok for d in sim.days for _, ok in d.quality)
    q_total = sum(len(d.quality) for d in sim.days)
    quality = f"{q_passed}/{q_total} met" if q_total else "not run (fake model)"
    out = [
        f"# Fidus simulation: {sim.name}",
        "",
        f"- **Model:** `{sim.model}`",
        f"- **Mode:** {mode}",
        f"- **Expectations:** {passed}/{total} met",
        f"- **Quality checks (expect_live):** {quality}",
        f"- **Unimplemented API calls hit:** {', '.join(sorted(set(sim.unhandled))) or 'none'}",
        "",
        "| Day | Date | Morning | Merged into sources | Night | Fidus PR after | Chapters changed | ✓ |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for d in sim.days:
        night = (
            d.command
            + (" (no-op)" if d.noop else "")
            + (" (paused)" if d.paused else "")
            + (" +audit" if d.audit else "")
            + ("" if d.exit_code == 0 else f" exit {d.exit_code}")
        )
        ok = "✅" if all(o for _, o in d.checks) else "❌"
        out.append(
            f"| {d.day} | {d.date} | {'; '.join(d.morning) or '–'} | {len(d.merged) or '–'} | "
            f"{night} | {d.pr} | {', '.join(d.changed_chapters) or '–'} | {ok if d.checks else ''} |"
        )
    for d in sim.days:
        out += ["", f"## Day {d.day} ({d.date})", ""]
        if d.morning:
            out += ["**Morning (reviewer):**"] + [f"- {m}" for m in d.morning] + [""]
        if d.merged:
            out += ["**Merged into source repos:**"] + [f"- {m}" for m in d.merged] + [""]
        out += [f"**Night:** `fidus {d.command}` → exit {d.exit_code}"]
        out += [f"    {s}" for s in d.summary]
        if d.stderr_tail:
            out += ["", "```", d.stderr_tail, "```"]
        out += [
            "",
            f"- **Fidus PR:** {d.pr}{' (**paused**)' if d.paused else ''}",
            f"- **Chapters changed tonight:** {', '.join(d.changed_chapters) or 'none'}",
            f"- **Retry queue:** {', '.join(d.retry) or 'empty'} · triage cache entries: {d.cache_entries}",
        ]
        if d.checks:
            out += [
                "- **Expectations:** "
                + " · ".join(f"{'✅' if ok else '❌'} `{n}`" for n, ok in d.checks)
            ]
        if d.quality:
            out += [
                "- **Quality (expect_live):** "
                + " · ".join(f"{'✅' if ok else '❌'} `{n}`" for n, ok in d.quality)
            ]
        if d.pr_body:
            out += [
                "",
                "<details><summary>PR description after this night</summary>",
                "",
                d.pr_body.replace("<!-- fidus:pr-body v1 -->", ""),
                "",
                "</details>",
            ]
    out += ["", "## Final book", ""]
    out += [
        f"- `{path}` ({len(text.split())} words)" for path, text in sorted(sim.final_docs.items())
    ]
    if sim.accuracy:
        a = sim.accuracy
        out += [
            "",
            "## Accuracy of the final book",
            "",
            f"Judge `{a.get('judge')}`: score **{a.get('score')}**, error rate {a.get('error_rate')}, "
            f"{a.get('chapters_judged')} chapters judged.",
        ]
    return "\n".join(out) + "\n"

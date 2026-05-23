---
name: skill-evolution-loop
description: Use when creating or improving a domain skill from real source artifacts, reference outputs, pressure-test failures, repeated tool-use mistakes, or unclear input/output contracts.
---

# Skill Evolution Loop

Use this skill to create or refine another skill from real work evidence. The goal is not to document one successful run; it is to make a future comparable tool run reach the right result with less user steering.

## Inputs

Useful evidence includes:

- the user's actual task and success criteria,
- source artifacts the future run will receive,
- reference outputs or templates the result must resemble,
- old prompts, scripts, docs, SQL, or manual steps that encode business logic,
- failed isolated runs, wrong outputs, confusion, or user corrections.

Separate stable facts from run-specific facts immediately. Stable facts belong in the skill. Month-specific values, sample customer names, one-off row counts, generated paths, temporary scripts, and debugging commands usually do not.

## Outputs

Create or update:

- `SKILL.md`: when to use the skill, required inputs, expected outputs, process, validation bar, and common failure modes.
- `references/`: only for durable contracts, formulas, schemas, template shapes, field mappings, or domain rules too detailed for `SKILL.md`.
- `scripts/`: only when deterministic execution is genuinely part of the skill, the operation is fragile, or the same code keeps being rewritten badly.
- `AGENTS.md`: only for routing, posture, and broad process reminders.

Do not turn a successful scratch implementation into a bundled script by default. A skill should preserve the result contract first; implementation freedom is useful when many approaches can produce the same validated output.

## Loop

1. Define the target.
   - Name the required source input, required outputs, and what "done" means.
   - State what is allowed to come from maintained config/defaults instead of the user-supplied input.

2. Read artifacts before writing guidance.
   - Inspect source files for real headers, metadata, footers, data types, and edge rows.
   - Inspect reference outputs for sheet names, column order, constants, formulas, date formats, remarks, total rows, and validation clues.
   - Extract concepts and invariants; avoid copying temporary values.

3. Draft the smallest skill that should change behavior.
   - Put trigger language in frontmatter.
   - Put stable input/output contracts and validation rules in the body.
   - Move detailed but durable mappings or schemas to `references/`.

4. Pressure test in isolation.
   - Run a fresh isolated attempt with only the task, artifacts, and skill access needed for the scenario.
   - Do not give away the intended answer unless the test is specifically about reproducing it.
   - Inspect the produced files yourself.

5. Revise from observed failures.
   - If the skill is hard to find, improve routing/description.
   - If the wrong artifact is produced, clarify output contract.
   - If unnecessary inputs are requested, clarify source boundaries.
   - If examples are overfit, remove drifting examples and state the stable invariant instead.
   - If deterministic help is needed every time, consider a script; if not, keep the skill implementation-agnostic.

6. Prune before calling it done.
   - Remove commands, paths, sample values, generated filenames, customer names, row counts, dates, and regression numbers that only describe one run.
   - Keep only details that should remain true for the next comparable task.

## Validation Bar

A skill revision is good when a fresh isolated attempt can:

- identify the right skill,
- understand the source input and required outputs,
- produce or plan an output with the right shape,
- validate totals, row counts, template structure, or schema as appropriate,
- report real gaps without treating expected maintained defaults as blockers.

Do not claim the skill works because you solved the task yourself. The evidence is the isolated run and the files or reasoning it produced.

## Common Mistakes

- Encoding one month's examples as permanent rules.
- Putting command transcripts in `AGENTS.md`.
- Hiding core requirements inside a script instead of the skill contract.
- Keeping obsolete SQL or app-tool references after changing strategy.
- Adding companion inputs because a reference workbook exists.
- Declaring victory before inspecting the isolated run's actual artifacts.

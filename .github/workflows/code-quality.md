---
name: code-quality
description: Make one concrete code-quality fix in NVX internal tooling
intent: Reduce maintainer effort with one novel, validated, low-risk correctness, maintainability, typing, or robustness improvement to NVX internal tooling without repeating rejected work.
on:
  schedule: hourly
  workflow_dispatch:
  skip-if-match:
    query: 'is:pr is:open "gh-aw-workflow-id: code-quality" in:body'
    max: 1
if: github.event_name != 'workflow_dispatch' || github.ref_name == 'dev'
permissions:
  contents: read
  issues: read
  pull-requests: read
  copilot-requests: write
strict: true
engine:
  id: copilot
  version: "1.0.86"
model: gpt-5.6-sol-fast
max-turns: 100
timeout-minutes: 60
concurrency: code-quality
sandbox:
  agent:
    id: awf
    model-fallback: false
    token-steering: false
imports:
  - uses: shared/code-improvement.md
    with:
      workflow-id: code-quality
evals:
  questions:
    - id: operational_value
      question: If the agent requested a pull request, does its output demonstrate that one draft pull request delivers one concrete correctness, maintainability, typing, or robustness improvement? Answer UNKNOWN if the agent called noop.
    - id: single_scope
      question: Does the agent output limit the run to at most one code-quality candidate?
    - id: evidenced_problem
      question: If the agent requested a pull request, does its output cite concrete evidence, such as a failing regression test, a Pyright diagnostic, or an exact code path, for the problem that the change fixes? Answer UNKNOWN if the agent called noop.
    - id: bounded_patch
      question: If the agent requested a pull request, does its output report that the final patch changes fewer than 100 total lines? Answer UNKNOWN if the agent called noop.
    - id: validation_passed
      question: If the agent requested a pull request, does its output report that every applicable post-edit validation command passed? Answer UNKNOWN if the agent called noop.
    - id: rejection_aware
      question: If the agent requested a pull request, does its output report that the change does not repeat an open, merged, or rejected pull request? Answer UNKNOWN if the agent called noop.
    - id: justified_noop
      question: If the agent called noop, does its output state the checked scope and a concrete reason that no candidate qualified? Answer UNKNOWN if the agent requested a pull request.
  model: small
---

# Code Quality

Make one concrete correctness, maintainability, typing, or robustness
improvement in the internal Python or allowlisted shell tooling.

A qualifying candidate fixes one demonstrable problem at its source:

- **Correctness or robustness:** a reachable input or state that produces a
  wrong result, an unhandled exception or raw traceback instead of a clear
  `ScriptError`, a misleading error, a leaked process or file handle, or a
  quoting or exit-status bug in an allowlisted shell script.
- **Typing:** an annotation, `cast`, or suppression that hides a real type
  mismatch that strict Pyright checks once it is corrected.
- **Maintainability:** a concrete hazard such as internal code proven unused
  across the whole repository.

Look in `scripts/nvx.py`, `scripts/nvx_tools/`, and the allowlisted
`scripts/setup/` and `guest/` scripts. Establish the problem from the exact
code path. For a Python behavior fix, reproduce it with a focused regression
test in the matching `scripts/test_*.py` module that fails before the fix and
passes after it. For a typing fix, show the strict Pyright diagnostic that the
hiding annotation, `cast`, or suppression masks; for unused code, show a
repository-wide search that finds no remaining reference. Guest scripts run
inside the MicroVM, which this workflow cannot boot, so select one only when
static evidence, ShellCheck, and shfmt fully establish the defect and its fix.
Preserve behavior for every valid input.

Reject style-only edits, formatting or renaming, annotation sweeps,
speculative hardening of unreachable paths, and changes that alter documented
CLI flags, output, or exit codes for valid use. Removing repeated logic belongs
to `code-deduplication`, and reusing an existing helper belongs to
`code-reusability`; do not select either here.

In the pull request body, name the problem, the triggering input or code path,
and the regression test or other evidence that proves the fix.

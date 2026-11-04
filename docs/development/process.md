# Development process

The brief (§14) prescribes an architecture-first, milestone-based process with tests early and documentation kept in
sync. This page states how that is practised in this repository.

## Method
1. Architecture before code: the package in `docs/` is the source of truth; changes to established decisions go
   through an ADR (new or amended) and `docs/14`.
2. One milestone at a time (`docs/12`). Each milestone starts with a short written plan (scope, files, acceptance
   tests, verification) that is reviewed and approved before implementation; it ends with a worklog entry.
3. Tests early: domain and application rules are written test-first; infrastructure is tested against the real
   containers; security suites are CI-blocking (doc 11).
4. Nothing is reported as done, measured or proven until it has been executed. Measured numbers replace estimates in
   the docs as soon as they exist (e.g., doc 02 §10 memory budget).

## AI-assisted development
Claude Code is the development assistant (brief §1). It proposes plans and diffs; the engineer reviews code, tests and
documentation and keeps accountability. Rules of engagement:
- Every task is given with the relevant doc sections, the acceptance tests to write first, the files expected to
  change and explicit non-goals.
- The assistant never marks a task done without executed tests and never fabricates results.
- Deviations from the architecture package are recorded (ADR amendment or `docs/14`) before implementation continues.
- Versions and licences are verified against primary sources and a real resolver, not read from memory (doc 02 §1).

## Definition of done (task and milestone)
Code + tests + docs in the same change · lint/type/import-linter clean · no new `skip` without an issue link · secrets
untouched · audit for new state-changing operations (from M1) · i18n keys for new UI strings · ADR updated if a decision
changed · worklog line · milestone exit criteria from `docs/12` demonstrated.

## Repository conventions
- Branch `main`; small, reviewable commits with descriptive messages; the assistant's commits carry a
  `Co-Authored-By` footer.
- `make lint` and `make test-unit` are the contract; `.github/workflows/ci.yml` mirrors them (optional, Q-13).
- Layering is enforced by import-linter contracts in `backend/pyproject.toml` (ADR-0002).
- Configuration only through environment variables documented in `deploy/.env.example`.

## Worklog
`docs/development/worklog/M<n>.md` per milestone: what was built, what changed versus the plan and why, measured
numbers, problems encountered and how they were solved, follow-ups. `docs/development/problems-encountered.md`
collects the cross-milestone lessons.

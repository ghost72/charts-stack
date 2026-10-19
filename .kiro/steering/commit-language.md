# Commit message and code language

charts-stack is a public, English-facing project. Write all git commit
messages for this repository in **English**.

- Applies to every commit in this repo, including those made by workflow agents.
- Use clear, conventional-style subjects (e.g. `fix(i18n): ...`,
  `docs: ...`, `feat: ...`) with an imperative mood.
- This rule is specific to charts-stack. Other (private, German) repos keep
  their own conventions.

## Code comments

Write all **code comments and docstrings** in English too (Python `#` comments
and `"""docstrings"""`, shell `#` comments). This applies to new and edited code
across all services and `watch.sh`.

- User-facing text is never hardcoded — it goes through the i18n `t()` helper
  and lives in `lang/{en,de,fr}.json`. German belongs ONLY in `lang/de.json`
  values (and the deliberate `CHART_META` playlist titles), never in code
  comments or runtime strings.

## Publishing order: NAS and live test before GitHub

GitHub is the public remote and must only ever receive changes the user has
verified in real operation. The order for any change to this repo is:

1. Agent commits locally on `main`.
2. The **user** pushes to the NAS remote (`origin`), deploys on the NAS, and
   tests the change live (dashboard / songrequest UI / Pushover as relevant).
3. Only AFTER the user confirms the live test passed does the agent push to the
   `github` remote.

Never push to `github` before the user has given the go-ahead from the live
NAS test. The agent may still commit locally and may push to `origin` only if
the user explicitly asks; the GitHub push always waits for the user's
post-deploy confirmation.

### "Tested" means a few days of real operation, not a quick check

A single successful run or a glance at a state file is NOT enough to call a
change tested. Several services run on scheduled loops (charts-download hourly,
charts-sync 12 h, charts-flashback 2 h, the Sunday hitparade run, backfills),
and some bugs only surface after a full cycle under load — e.g. the 1.2.0
`charts-download` change looked fine in a short check (songs appeared in the
state file) but actually spun in an infinite loop that only became visible once
a run had been going for a while. That broken version reached GitHub as "latest"
and needed an emergency 1.2.1.

Therefore:

- Let a change run on the NAS through **at least one full real cycle of every
  service it touches** (ideally a couple of days of normal operation), not just
  one manual trigger.
- For `charts-download`/`charts-upgrade` changes, confirm a scheduled run
  actually completes (reaches "Lauf fertig"), CPU returns to idle, and songs are
  enqueued — do not infer success from the state file alone.
- The agent must not report a change as "live tested ✓" or push to GitHub on the
  strength of a quick look. GitHub only ever gets changes that have survived
  real, multi-run operation and the user's explicit go-ahead.

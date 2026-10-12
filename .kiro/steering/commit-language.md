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

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

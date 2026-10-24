# Contributing to charts-stack

Thanks for your interest. This is a small, solo-maintained hobby project, so
contributions are very welcome but kept simple. A few conventions keep the repo
consistent – please skim them before opening a pull request.

## Language

- **Commits, code comments and docstrings are written in English.** This keeps
  the codebase approachable for everyone.
- **User-facing text is never hardcoded.** Every string a user sees (log
  messages, Pushover notifications, dashboard/UI text) goes through the i18n
  `t()` helper and lives in `lang/en.json`, `lang/de.json` and `lang/fr.json`.
  Never put a visible string directly in the code.

## i18n parity (important)

The three language files must stay **key-for-key equal** – they currently carry
**932 keys each**. When you add a user-facing string:

1. Add the new key to **all three** files (`en`, `de`, `fr`) with a real
   translation (not a copy of the English text).
2. Keep placeholders identical across files: `{name}`-style fields (Python
   services) and `%s` (the `watchdog.*` keys only) must match `en.json` in count
   and – for `{name}` – in name.

A test (`charts-sync/tests/test_i18n.py`, and the per-service `test_i18n.py`
suites) enforces that all language files share the same keys and placeholders.

## Documentation language convention

- The root `README.md` is **one bilingual file** with a `## English` section and
  a `## Deutsch` section (plus a shared header). Edit both sections together so
  they stay in parity.
- Per-service docs come in pairs: `README.md` (German) and `README.en.md`
  (English). Keep the two in sync when you change one.

## Running the tests

Each service has its own `tests/` pytest suite. Run the relevant one before
opening a PR. For example, for `charts-sync`:

```bash
cd charts-sync
python3 -m venv .venv && source .venv/bin/activate
pip install pytest
pip install -r requirements.txt   # if the service has one
pytest
```

Run the suite for every service you touched.

## How to contribute

1. Fork the repo (or create a branch) and base your work on `main`.
2. Keep each change focused and match the existing style – don't refactor
   unrelated code.
3. Open a pull request against `main`.
4. The maintainer reviews and tests the change before merging. Because the stack
   runs scheduled loops, a change may be verified in live operation before it is
   accepted – please be patient.

That's it. Friendly, focused PRs are the easiest to merge. Thank you!

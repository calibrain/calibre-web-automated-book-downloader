# AGENT.md

## Interface text

Never hardcode user-facing strings. All of them go through `t()` from `src/frontend/src/i18n`,
and every string is added to the JSON catalogs in `src/frontend/src/i18n/locales/` (keys are the
literal English text; `en.json` mirrors them). Run `make frontend-test` — the prek hook runs the
same check on commit and fails on a missing catalog entry. See `docs/dev/translations.md`.

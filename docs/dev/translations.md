# Translating the interface

Translations are flat JSON catalogs in `src/frontend/src/i18n/locales/`, one per language,
mapping the English text (the key) to its translation. English needs no catalog: text with no
entry shows in English. `en.json` mirrors the keys of every catalog (key = value) as the source
file for translation platforms. The language is picked from the browser's preferred languages,
falling back to English.

## Add a language

1. Copy `locales/es.json` to `locales/<code>.json` and translate the values. `<code>` is the
   language's ISO 639-1 code: `fr` for `fr-FR`.
2. Register it in `src/frontend/src/i18n/index.ts`:

   ```ts
   import fr from './locales/fr.json';

   const catalogs: Record<string, Catalog> = { es, fr };
   ```

3. Run `make frontend-checks frontend-test`.

## Make a component translatable

Wrap the English text in `t()`. A value goes in braces:

```tsx
<label htmlFor="password">{t('Password')}</label>;
<p>{t('{count} books selected', { count })}</p>;
```

Then add the text to every catalog:

```json
"Password": "Contraseña",
"{count} books selected": "{count} libros seleccionados"
```

Text the backend sends needs no `t()`: settings labels and descriptions, and API errors, are
translated when they arrive. Add their English text to the catalogs the same way. A message with
a value in it, like `Failed to connect to example.org`, matches the entry
`"Failed to reach {host}"`.

## Enforcement

`make frontend-test` fails on any text passed to `t()` that a catalog does not have, and on an
`en.json` out of sync. The `i18n-check` git hook (prek, `make install-python-dev`) runs the same
check on commit, so new hardcoded strings cannot land.

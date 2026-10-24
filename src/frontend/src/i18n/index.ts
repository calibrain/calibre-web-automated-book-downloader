import es from './locales/es.json';

// A language's translations, keyed by the English text. Text with no entry
// shows in English. Flat JSON catalogs (src/i18n/locales/*.json) so tools
// like Weblate or Crowdin can read and write them.
export type Catalog = Record<string, string>;

const catalogs: Record<string, Catalog> = { es };

// The browser's preferred languages in order, matched on the primary subtag
// (es-MX is es), then English.
export const resolveLocale = (preferred: readonly string[]): string =>
  preferred
    .map((tag) => tag.toLowerCase().split('-')[0])
    .find((code) => Object.hasOwn(catalogs, code)) ?? 'en';

const fill = (text: string, values: Record<string, string | number>) =>
  text.replace(/\{(\w+)\}/g, (placeholder, name: string) =>
    name in values ? String(values[name]) : placeholder,
  );

// Own entries only: "toString" is not a translation.
const lookup = (catalog: Catalog, text: string) =>
  Object.hasOwn(catalog, text) ? catalog[text] : undefined;

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// Backend text with a value in it ("Failed to connect to example.org") matches a
// {name} template; the template with the most fixed text wins. A linear scan.
function matchTemplate(catalog: Catalog, text: string): string | undefined {
  let best: { fixed: number; result: string } | undefined;
  for (const [template, translation] of Object.entries(catalog)) {
    if (!template.includes('{')) continue;
    const names = [...template.matchAll(/\{(\w+)\}/g)].map((m) => m[1]);
    const parts = template.split(/\{\w+\}/);
    const fixed = parts.join('').length;
    if (best && best.fixed >= fixed) continue;
    const match = new RegExp(`^${parts.map(escape).join('(.+?)')}$`, 's').exec(text);
    if (match) {
      const values = Object.fromEntries(
        names.map((name, i) => [name, lookup(catalog, match[i + 1]) ?? match[i + 1]]),
      );
      best = { fixed, result: fill(translation, values) };
    }
  }
  return best?.result;
}

export function translate(
  catalog: Catalog,
  text: string,
  values?: Record<string, string | number>,
): string {
  const translation = lookup(catalog, text);
  if (translation === undefined && !values) return matchTemplate(catalog, text) ?? text;
  return fill(translation ?? text, values ?? {});
}

// Read once, at load: changing the language is a reload.
export const locale = resolveLocale(navigator.languages);

if (typeof document !== 'undefined') document.documentElement.lang = locale;

const active = catalogs[locale] ?? {};

// The English text in the browser's language, each {name} replaced by values.name.
export function t(text: string, values?: Record<string, string | number>): string {
  return translate(active, text, values);
}

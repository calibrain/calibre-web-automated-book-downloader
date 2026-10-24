import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, it, expect } from 'vitest';

import { resolveLocale, translate } from '../i18n/index';
import en from '../i18n/locales/en.json';
import es from '../i18n/locales/es.json';

const repoRoot = join(__dirname, '../../../..');

// Every literal passed to t() in the source, the way a translator sees them.
const sourceStrings = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceStrings(path);
    if (!/\.tsx?$/.test(entry.name)) return [];
    return [...readFileSync(path, 'utf8').matchAll(/\bt\(\s*(['"])((?:\\.|(?!\1).)*)\1/g)].map(
      (match) => match[2],
    );
  });

// Every source file under a directory, as one string each.
const sources = (dir: string, test: RegExp): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sources(path, test);
    return test.test(entry.name) ? [readFileSync(path, 'utf8')] : [];
  });

describe('i18n', () => {
  it('picks the first browser language with a catalog, by primary subtag, else English', () => {
    expect(resolveLocale(['fr-FR', 'es-MX', 'en'])).toBe('es');
    expect(resolveLocale(['ES-es'])).toBe('es');
    expect(resolveLocale(['fr', 'de'])).toBe('en');
    expect(resolveLocale([])).toBe('en');
  });

  it('translates, fills values, and matches a backend message to its template', () => {
    const catalog = {
      Hide: 'Ocultar',
      timeout: 'tiempo agotado',
      '{count} books': '{count} libros',
      '{title}: {message}': '{title}: {message}',
      'Failed to reach {host}': 'No se pudo llegar a {host}',
    };
    expect(translate(catalog, 'Hide')).toBe('Ocultar');
    expect(translate(catalog, '{count} books', { count: 3 })).toBe('3 libros');
    expect(translate(catalog, 'Failed to reach example.org')).toBe(
      'No se pudo llegar a example.org',
    );
    expect(translate(catalog, 'Failed to reach example.org: timeout')).toBe(
      'No se pudo llegar a example.org: timeout',
    );
    expect(translate(catalog, 'Hide: timeout')).toBe('Ocultar: tiempo agotado');
    expect(translate(catalog, 'toString')).toBe('toString');
    expect(translate(catalog, 'Untranslated {x}', { x: 1 })).toBe('Untranslated 1');
    expect(translate(catalog, 'Nothing like it')).toBe('Nothing like it');
  });

  it('has a Spanish translation for every string the interface passes to t()', () => {
    const missing = [...new Set(sourceStrings(join(__dirname, '..')))].filter((s) => !(s in es));
    expect(missing).toEqual([]);
  });

  it('keeps en.json (the Weblate source file) in sync: same keys as es.json, key = value', () => {
    expect(Object.keys(en)).toEqual(Object.keys(es));
    expect(Object.entries(en).every(([key, value]) => key === value)).toBe(true);
  });

  // Informational, not a failure: backend text is composed at runtime, so a
  // static scan cannot prove an entry unused. The printed candidates are for a
  // human to check before removing.
  it('reports catalog entries no source references', () => {
    const literals = new Set(sourceStrings(join(__dirname, '..')));
    const frontend = sources(join(__dirname, '..'), /\.tsx?$/);
    // Python joins adjacent string literals across lines; merge those first.
    const backend = sources(join(repoRoot, 'shelfmark'), /\.py$/).map((s) =>
      s.replace(/["']\s*\n\s*["']/g, ''),
    );
    const data = readdirSync(join(repoRoot, 'data'))
      .filter((name) => name.endsWith('.json'))
      .map((name) => readFileSync(join(repoRoot, 'data', name), 'utf8'));
    const referenced = (key: string) =>
      literals.has(key) ||
      [...frontend, ...backend, ...data].some((s) => s.includes(key)) ||
      // A backend template like "Books destination is writable: {path}" is
      // built with other variable names, so match its fixed parts.
      (key.includes('{') &&
        (() => {
          const parts = key.split(/\{\w+\}/).filter((part) => part.trim());
          return parts.length > 0 && backend.some((s) => parts.every((part) => s.includes(part)));
        })());
    const candidates = Object.keys(es).filter((key) => !referenced(key));
    if (candidates.length > 0) {
      console.warn(`es.json entries no source references (${candidates.length}):`);
      for (const key of candidates) console.warn(`  ${JSON.stringify(key)}`);
    }
  });
});

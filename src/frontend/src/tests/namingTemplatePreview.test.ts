import { describe, expect, it } from 'vitest';

import {
  buildNamingTemplatePreview,
  NAMING_TEMPLATE_TOKENS,
  renderNamingTemplate,
  SAMPLE_NAMING_METADATA,
} from '../utils/namingTemplatePreview';

describe('namingTemplatePreview', () => {
  it('groups primary title with universal variables', () => {
    expect(NAMING_TEMPLATE_TOKENS.find((token) => token.token === 'PrimaryTitle')?.group).toBe(
      'Universal',
    );
  });

  it('renders primary title in path previews', () => {
    const preview = buildNamingTemplatePreview(
      '{Author}/{Series/}{SeriesPosition - }{PrimaryTitle} ({Year})',
      'path',
      'book',
    );

    expect(preview.value).toBe(
      'Arthur Conan Doyle/Sherlock Holmes/5 - The Hound of the Baskervilles (1902).epub',
    );
  });

  it('omits conditional text when a variable is empty', () => {
    const preview = renderNamingTemplate(
      '{Author}/{Series/}{PrimaryTitle}{ - Subtitle}',
      {
        ...SAMPLE_NAMING_METADATA,
        Series: '',
        Subtitle: '',
      },
      { allowPathSeparators: true },
    );

    expect(preview.value).toBe('Arthur Conan Doyle/The Hound of the Baskervilles');
  });

  it('keeps only the first of several authors for FirstAuthor', () => {
    const preview = renderNamingTemplate(
      '{FirstAuthor}/{Year}',
      { ...SAMPLE_NAMING_METADATA, Author: 'Terry Pratchett, Neil Gaiman', FirstAuthor: '' },
      { allowPathSeparators: true },
    );

    expect(preview.value).toBe('Terry Pratchett/1902');
  });

  it('offers FirstAuthor as a core variable', () => {
    const token = NAMING_TEMPLATE_TOKENS.find((t) => t.token === 'FirstAuthor');

    expect(token?.group).toBe('Core');
    expect(token?.audiobookOnly).toBeFalsy();
  });

  it('reports unknown bare variables', () => {
    const preview = renderNamingTemplate('{Author}/{NotAThing}', SAMPLE_NAMING_METADATA, {
      allowPathSeparators: true,
    });

    expect(preview.unknownTokens).toEqual(['NotAThing']);
    expect(preview.value).toBe('Arthur Conan Doyle');
  });

  it('offers Language as a core variable for both content types', () => {
    const language = NAMING_TEMPLATE_TOKENS.find((token) => token.token === 'Language');

    expect(language?.group).toBe('Core');
    expect(language?.audiobookOnly).toBeFalsy();
  });

  it('separates translated editions into their own folder', () => {
    const template = '{Author}/{Title}{ (Language)}';

    const swedish = renderNamingTemplate(
      template,
      {
        ...SAMPLE_NAMING_METADATA,
        Author: 'Andy Weir',
        Title: 'Project Hail Mary',
        Language: 'sv',
      },
      { allowPathSeparators: true },
    );
    const english = renderNamingTemplate(
      template,
      { ...SAMPLE_NAMING_METADATA, Author: 'Andy Weir', Title: 'Project Hail Mary', Language: '' },
      { allowPathSeparators: true },
    );

    expect(swedish.value).toBe('Andy Weir/Project Hail Mary (sv)');
    expect(english.value).toBe('Andy Weir/Project Hail Mary');
    expect(swedish.value).not.toBe(english.value);
  });

  it('keeps the picker and the known-token list in lockstep', () => {
    // KNOWN_TOKENS is a hand-maintained duplicate of the Python list. A token
    // added to the picker but not to it would render as an unknown variable.
    for (const token of NAMING_TEMPLATE_TOKENS) {
      const preview = renderNamingTemplate(`{${token.token}}`, SAMPLE_NAMING_METADATA, {
        allowPathSeparators: true,
      });

      expect(preview.unknownTokens, `${token.token} is missing from KNOWN_TOKENS`).toEqual([]);
    }
  });
});

import { describe, expect, it } from 'vitest';

import { buildUrlSearchHash } from '../utils/urlSearchHash';

describe('buildUrlSearchHash', () => {
  it('builds a hash reflecting query, search_by and a non-default content_type', () => {
    const hash = buildUrlSearchHash({
      queryValue: 'dune',
      searchBy: 'manual',
      contentType: 'audiobook',
      combinedMode: false,
      advancedFilters: {},
    });

    expect(new URLSearchParams(hash).get('q')).toBe('dune');
    expect(new URLSearchParams(hash).get('search_by')).toBe('manual');
    expect(new URLSearchParams(hash).get('content_type')).toBe('audiobook');
  });

  it('omits content_type when it is the ebook default', () => {
    const hash = buildUrlSearchHash({
      queryValue: 'dune',
      searchBy: 'general',
      contentType: 'ebook',
      combinedMode: false,
      advancedFilters: {},
    });

    expect(new URLSearchParams(hash).has('content_type')).toBe(false);
  });

  it('omits search_by when it is the general default', () => {
    const hash = buildUrlSearchHash({
      queryValue: 'dune',
      searchBy: 'general',
      contentType: 'ebook',
      combinedMode: false,
      advancedFilters: {},
    });

    expect(new URLSearchParams(hash).has('search_by')).toBe(false);
  });

  it('sets content_type=combined when combinedMode is true', () => {
    const hash = buildUrlSearchHash({
      queryValue: 'dune',
      searchBy: 'general',
      contentType: 'ebook',
      combinedMode: true,
      advancedFilters: {},
    });

    expect(new URLSearchParams(hash).get('content_type')).toBe('combined');
  });

  it('mirrors advanced filters (isbn/author/title/sort/content/lang/format)', () => {
    const hash = buildUrlSearchHash({
      queryValue: '',
      searchBy: 'author',
      contentType: 'ebook',
      combinedMode: false,
      advancedFilters: {
        author: 'frank herbert',
        lang: ['en', 'de'],
        formats: ['epub'],
        sort: 'newest',
      },
    });

    const params = new URLSearchParams(hash);
    expect(params.get('author')).toBe('frank herbert');
    expect(params.get('sort')).toBe('newest');
    expect(params.getAll('lang')).toEqual(['en', 'de']);
    expect(params.getAll('format')).toEqual(['epub']);
  });

  it('produces an empty string when there is nothing to reflect', () => {
    const hash = buildUrlSearchHash({
      queryValue: '',
      searchBy: 'general',
      contentType: 'ebook',
      combinedMode: false,
      advancedFilters: {},
    });

    expect(hash).toBe('');
  });

  it('serializes a non-text provider field value so the target round-trips', () => {
    const hash = buildUrlSearchHash({
      queryValue: 'id:1234',
      searchBy: 'series',
      contentType: 'ebook',
      combinedMode: false,
      advancedFilters: {},
    });

    expect(new URLSearchParams(hash).get('q')).toBe('id:1234');
    expect(new URLSearchParams(hash).get('search_by')).toBe('series');
  });

  it('serializes numeric and checkbox field values', () => {
    const numeric = buildUrlSearchHash({
      queryValue: 2024,
      searchBy: 'year',
      contentType: 'ebook',
      combinedMode: false,
      advancedFilters: {},
    });
    expect(new URLSearchParams(numeric).get('q')).toBe('2024');

    const checked = buildUrlSearchHash({
      queryValue: true,
      searchBy: 'signed',
      contentType: 'ebook',
      combinedMode: false,
      advancedFilters: {},
    });
    expect(new URLSearchParams(checked).get('q')).toBe('1');

    const unchecked = buildUrlSearchHash({
      queryValue: false,
      searchBy: 'signed',
      contentType: 'ebook',
      combinedMode: false,
      advancedFilters: {},
    });
    expect(new URLSearchParams(unchecked).has('q')).toBe(false);
  });

  it('omits filters still sitting at their defaults', () => {
    const hash = buildUrlSearchHash({
      queryValue: 'dune',
      searchBy: 'general',
      contentType: 'ebook',
      combinedMode: false,
      defaultSort: 'relevance',
      defaultFormats: ['epub', 'mobi', 'azw3'],
      advancedFilters: {
        sort: 'relevance',
        lang: ['default'],
        formats: ['epub', 'mobi', 'azw3'],
      },
    });

    expect(hash).toBe('q=dune');
  });

  it('treats a reordered default selection as the default', () => {
    const hash = buildUrlSearchHash({
      queryValue: 'dune',
      searchBy: 'general',
      contentType: 'ebook',
      combinedMode: false,
      defaultFormats: ['epub', 'mobi', 'azw3'],
      advancedFilters: { formats: ['azw3', 'epub', 'mobi'] },
    });

    expect(hash).toBe('q=dune');
  });

  it('keeps filters the user actually changed', () => {
    const params = new URLSearchParams(
      buildUrlSearchHash({
        queryValue: 'dune',
        searchBy: 'general',
        contentType: 'ebook',
        combinedMode: false,
        defaultSort: 'relevance',
        defaultFormats: ['epub', 'mobi', 'azw3'],
        advancedFilters: {
          sort: 'newest',
          lang: ['en', 'de'],
          formats: ['epub'],
        },
      }),
    );

    expect(params.get('sort')).toBe('newest');
    expect(params.getAll('lang')).toEqual(['en', 'de']);
    expect(params.getAll('format')).toEqual(['epub']);
  });

  it('keeps a narrowed selection that happens to be the same length', () => {
    const params = new URLSearchParams(
      buildUrlSearchHash({
        queryValue: 'dune',
        searchBy: 'general',
        contentType: 'ebook',
        combinedMode: false,
        defaultFormats: ['epub', 'mobi', 'azw3'],
        advancedFilters: { formats: ['epub', 'mobi', 'pdf'] },
      }),
    );

    expect(params.getAll('format')).toEqual(['epub', 'mobi', 'pdf']);
  });

  it('leaves a plain default-state search with no hash at all', () => {
    const hash = buildUrlSearchHash({
      queryValue: '',
      searchBy: 'general',
      contentType: 'ebook',
      combinedMode: false,
      defaultSort: 'relevance',
      defaultFormats: ['epub', 'mobi', 'azw3'],
      advancedFilters: {
        sort: 'relevance',
        lang: ['default'],
        formats: ['epub', 'mobi', 'azw3'],
      },
    });

    expect(hash).toBe('');
  });
});

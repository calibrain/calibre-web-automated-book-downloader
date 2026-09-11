import type { AdvancedFilterState, ContentType } from '../types';
import { LANGUAGE_OPTION_DEFAULT } from './languageFilters';

export interface UrlSearchHashState {
  /**
   * Value of the active "Search By" target - the text input for general/direct/text
   * fields, or the selected provider-field value. Serialized as `q` so a link
   * round-trips back into whichever target `searchBy` names.
   */
  queryValue: string | number | boolean;
  searchBy: string;
  contentType: ContentType;
  combinedMode: boolean;
  advancedFilters: Partial<AdvancedFilterState>;
  /**
   * Sort the app would apply on its own (the provider's default in Universal mode,
   * the configured one in Direct). A sort matching it is left out of the hash.
   */
  defaultSort?: string;
  /**
   * Format selection the app starts from - `supported_formats` from the server config,
   * not a fixed list, so an instance with a custom format list still gets a clean URL.
   */
  defaultFormats?: string[];
}

/** Selections are order-independent, so compare them as sets. */
const isDefaultSelection = (values: string[] | undefined, defaults: string[]): boolean => {
  if (!values) {
    return true;
  }
  if (values.length !== defaults.length) {
    return false;
  }
  const defaultSet = new Set(defaults);
  return values.every((value) => defaultSet.has(value));
};

const serializeQueryValue = (value: string | number | boolean): string => {
  if (typeof value === 'boolean') {
    return value ? '1' : '';
  }
  return typeof value === 'number' ? String(value) : value;
};

/**
 * Build the URL hash fragment (without the leading `#`) that mirrors the
 * current search state, using the same param names parseUrlSearchParams reads.
 *
 * Kept as a hash fragment (not query params) so it stays browser-side only,
 * rather than looking like a server-processed query string.
 *
 * Filters still sitting at their defaults are left out: they say nothing about what
 * the user is looking at, and carrying every format and `lang=default` turns a plain
 * search into a link several times longer than the query it shares.
 */
export const buildUrlSearchHash = (state: UrlSearchHashState): string => {
  const params = new URLSearchParams();

  const queryValue = serializeQueryValue(state.queryValue);
  if (queryValue) {
    params.set('q', queryValue);
  }
  if (state.searchBy && state.searchBy !== 'general') {
    params.set('search_by', state.searchBy);
  }
  if (state.combinedMode) {
    params.set('content_type', 'combined');
  } else if (state.contentType && state.contentType !== 'ebook') {
    // 'ebook' is the app's default content type - omit it like 'general' search_by,
    // so a plain default-state URL doesn't carry a hash at all.
    params.set('content_type', state.contentType);
  }

  const { isbn, author, title, sort, content, lang, formats } = state.advancedFilters;
  if (isbn) params.set('isbn', isbn);
  if (author) params.set('author', author);
  if (title) params.set('title', title);
  if (sort && sort !== state.defaultSort) params.set('sort', sort);
  if (content) params.set('content', content);

  // `[LANGUAGE_OPTION_DEFAULT]` is the untouched language selection, and it already means
  // "whatever the server default is" - spelling it out in the URL adds nothing.
  if (!isDefaultSelection(lang, [LANGUAGE_OPTION_DEFAULT])) {
    for (const value of lang ?? []) {
      if (value) params.append('lang', value);
    }
  }
  if (!isDefaultSelection(formats, state.defaultFormats ?? [])) {
    for (const value of formats ?? []) {
      if (value) params.append('format', value);
    }
  }

  return params.toString();
};

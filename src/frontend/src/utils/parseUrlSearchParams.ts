import { AdvancedFilterState } from '../types';

/**
 * Parsed search parameters from URL
 */
export interface ParsedUrlSearch {
  searchInput: string;
  advancedFilters: Partial<AdvancedFilterState>;
  hasSearchParams: boolean;
}

/**
 * Parse URL search parameters into search state.
 *
 * Supports both Direct Download and Universal mode parameters.
 * In Universal mode, only query and sort are used (others are parsed but
 * ignored by buildSearchQuery).
 *
 * @example
 * // Direct mode: /?q=harry+potter&author=rowling&format=epub&lang=en
 * // Universal mode: /?q=dune&sort=popularity
 */
export function parseUrlSearchParams(searchParams: URLSearchParams): ParsedUrlSearch {
  const result: ParsedUrlSearch = {
    searchInput: '',
    advancedFilters: {},
    hasSearchParams: false,
  };

  // Parse main query (support both 'q' and 'query' for flexibility)
  const query = searchParams.get('q') || searchParams.get('query') || '';
  if (query) {
    result.searchInput = query;
    result.hasSearchParams = true;
  }

  // Parse single-value filters
  const isbn = searchParams.get('isbn');
  const author = searchParams.get('author');
  const title = searchParams.get('title');
  const sort = searchParams.get('sort');
  const content = searchParams.get('content');

  if (isbn) {
    result.advancedFilters.isbn = isbn;
    result.hasSearchParams = true;
  }
  if (author) {
    result.advancedFilters.author = author;
    result.hasSearchParams = true;
  }
  if (title) {
    result.advancedFilters.title = title;
    result.hasSearchParams = true;
  }
  if (sort) {
    result.advancedFilters.sort = sort;
    result.hasSearchParams = true;
  }
  if (content) {
    result.advancedFilters.content = content;
    result.hasSearchParams = true;
  }

  // Parse multi-value filters (can appear multiple times in URL)
  // e.g., /?lang=en&lang=de or /?format=epub&format=mobi
  const langValues = searchParams.getAll('lang').filter(Boolean);
  const formatValues = searchParams.getAll('format').filter(Boolean);

  if (langValues.length > 0) {
    result.advancedFilters.lang = langValues;
    result.hasSearchParams = true;
  }
  if (formatValues.length > 0) {
    result.advancedFilters.formats = formatValues;
    result.hasSearchParams = true;
  }

  return result;
}

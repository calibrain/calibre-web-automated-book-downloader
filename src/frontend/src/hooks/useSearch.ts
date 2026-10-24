import { useState, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';

import { DEFAULT_SUPPORTED_FORMATS } from '../data/languages';
import { t } from '../i18n';
import { searchBooks, searchMetadata, AuthenticationError } from '../services/api';
import type { Book, AppConfig, AdvancedFilterState, ContentType, SearchMode } from '../types';
import { LANGUAGE_OPTION_DEFAULT } from '../utils/languageFilters';
import { describeSearchFailure } from '../utils/searchFailureMessage';

const DEFAULT_FORMAT_SELECTION = DEFAULT_SUPPORTED_FORMATS;

interface UseSearchOptions {
  showToast: (message: string, type: 'info' | 'success' | 'error') => void;
  setIsAuthenticated: (value: boolean) => void;
  authRequired: boolean;
  onSearchReset?: () => void;
  contentType?: ContentType;
}

// Search field values for universal mode (provider-specific fields)
type SearchFieldValues = Record<string, string | number | boolean>;

interface UseSearchReturn {
  books: Book[];
  setBooks: React.Dispatch<React.SetStateAction<Book[]>>;
  isSearching: boolean;
  lastSearchQuery: string;
  searchInput: string;
  setSearchInput: (value: string) => void;
  showAdvanced: boolean;
  setShowAdvanced: (value: boolean) => void;
  advancedFilters: AdvancedFilterState;
  setAdvancedFilters: React.Dispatch<React.SetStateAction<AdvancedFilterState>>;
  updateAdvancedFilters: (updates: Partial<AdvancedFilterState>) => void;
  handleSearch: (params: {
    query: string;
    config: AppConfig | null;
    fieldValues?: Record<string, string | number | boolean>;
    contentTypeOverride?: ContentType;
    searchMode?: SearchMode;
    providerOverride?: string;
    sort?: string;
  }) => Promise<void>;
  handleResetSearch: (config: AppConfig | null) => void;
  resetSortFilter: () => void;
  reSortByDownloads: () => void;
  // Universal mode search field values
  searchFieldValues: SearchFieldValues;
  updateSearchFieldValue: (key: string, value: string | number | boolean, label?: string) => void;
  searchFieldLabels: Record<string, string>;
  // Pagination (universal mode only)
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMore: (config: AppConfig | null, searchMode?: SearchMode) => Promise<void>;
  totalFound: number;
  // Direct mode: total result count from release sources (e.g., "500+" for capped)
  directTotalResults: number | string | null;
  // Source URL and title for the current result set (e.g. Hardcover list page)
  resultsSourceUrl: string | undefined;
  resultsSourceTitle: string | undefined;
}

export function useSearch(options: UseSearchOptions): UseSearchReturn {
  const {
    showToast,
    setIsAuthenticated,
    authRequired,
    onSearchReset,
    contentType = 'ebook',
  } = options;
  const navigate = useNavigate();

  const [books, setBooks] = useState<Book[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [lastSearchQuery, setLastSearchQuery] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [advancedFilters, setAdvancedFilters] = useState({
    isbn: '',
    author: '',
    title: '',
    lang: [LANGUAGE_OPTION_DEFAULT],
    sort: '',
    content: '',
    formats: DEFAULT_FORMAT_SELECTION,
  });

  // Universal mode: provider-specific search field values
  const [searchFieldValues, setSearchFieldValues] = useState<SearchFieldValues>({});
  const [searchFieldLabels, setSearchFieldLabels] = useState<Record<string, string>>({});

  // Pagination state (universal mode only)
  const [currentPage, setCurrentPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [totalFound, setTotalFound] = useState(0);
  // Direct mode: total result count from release sources (e.g., Anna's Archive)
  const [directTotalResults, setDirectTotalResults] = useState<number | string | null>(null);
  const [resultsSourceUrl, setResultsSourceUrl] = useState<string | undefined>();
  const [resultsSourceTitle, setResultsSourceTitle] = useState<string | undefined>();

  // Store last search params for loadMore
  const lastSearchParamsRef = useRef<{
    query: string;
    sort: string;
    fieldValues: SearchFieldValues;
    providerOverride?: string;
    contentType: ContentType;
  } | null>(null);

  const updateAdvancedFilters = useCallback((updates: Partial<AdvancedFilterState>) => {
    setAdvancedFilters((prev) => ({ ...prev, ...updates }));
  }, []);

  // Sort books by downloads descending (no downloads go to the end)
  const sortBooksByDownloads = useCallback((bookList: Book[]): Book[] => {
    const sorted = bookList.toSorted((a, b) => {
      const aDownloads = a.downloads ?? 0;
      const bDownloads = b.downloads ?? 0;
      // Books with downloads come first, sorted by count descending
      if (aDownloads > 0 && bDownloads === 0) return -1;
      if (aDownloads === 0 && bDownloads > 0) return 1;
      return bDownloads - aDownloads;
    });
    return sorted;
  }, []);

  // Re-sort current books by downloads descending (client-side only, no search)
  const reSortByDownloads = useCallback(() => {
    setBooks((prev) => sortBooksByDownloads(prev));
  }, [sortBooksByDownloads]);

  const updateSearchFieldValue = useCallback(
    (key: string, value: string | number | boolean, label?: string) => {
      setSearchFieldValues((prev) => ({ ...prev, [key]: value }));
      setSearchFieldLabels((prev) => {
        const next = { ...prev };
        if (label !== undefined) {
          if (label) {
            next[key] = label;
          } else {
            delete next[key];
          }
          return next;
        }

        delete next[key];
        return next;
      });
    },
    [],
  );

  const resetSortFilter = useCallback(() => {
    setAdvancedFilters((prev) => ({ ...prev, sort: '' }));
  }, []);

  // Helper to handle authentication and other errors consistently
  const handleSearchError = useCallback(
    (error: unknown, context: string) => {
      if (error instanceof AuthenticationError) {
        setIsAuthenticated(false);
        if (authRequired) {
          void navigate('/login', { replace: true });
        }
        return;
      }

      console.error(`${context}:`, error);
      const message = error instanceof Error ? error.message : t(context);
      showToast(message, 'error');
    },
    [setIsAuthenticated, authRequired, navigate, showToast],
  );

  const handleSearch = useCallback(
    async ({
      query,
      config,
      fieldValues,
      contentTypeOverride,
      searchMode: searchModeOverride,
      providerOverride,
      sort,
    }: {
      query: string;
      config: AppConfig | null;
      fieldValues?: Record<string, string | number | boolean>;
      contentTypeOverride?: ContentType;
      searchMode?: SearchMode;
      providerOverride?: string;
      sort?: string;
    }) => {
      const effectiveContentType = contentTypeOverride ?? contentType;
      const searchMode = (searchModeOverride ?? config?.search_mode) || 'universal';

      // In universal mode, check if we have either a query or field values
      if (searchMode === 'universal') {
        const params = new URLSearchParams(query);
        const searchQuery = params.get('query') || '';
        // Use explicitly passed fieldValues if provided, otherwise fall back to state
        const effectiveFieldValues = fieldValues ?? searchFieldValues;
        const hasFieldValues = Object.values(effectiveFieldValues).some(
          (v) => v !== '' && v !== false,
        );
        const sortOrder = params.get('sort') || 'relevance';

        if (!searchQuery && !hasFieldValues) {
          setBooks([]);
          setLastSearchQuery('');
          setHasMore(false);
          setTotalFound(0);
          setDirectTotalResults(null);
          setCurrentPage(1);
          setResultsSourceUrl(undefined);
          setResultsSourceTitle(undefined);
          lastSearchParamsRef.current = null;
          return;
        }

        setIsSearching(true);
        setLastSearchQuery(query);
        // Reset pagination for new search. directTotalResults belongs to direct mode
        // and is never written here, so without clearing it a count from an earlier
        // direct search survives the mode switch and renders over these results.
        setCurrentPage(1);
        setHasMore(false);
        setTotalFound(0);
        setDirectTotalResults(null);

        try {
          const result = await searchMetadata(
            searchQuery,
            40,
            sortOrder,
            effectiveFieldValues,
            1,
            effectiveContentType,
            providerOverride,
          );
          if (result.books.length > 0) {
            setBooks(result.books);
            setHasMore(result.hasMore);
            setTotalFound(result.totalFound);
            setResultsSourceUrl(result.sourceUrl);
            setResultsSourceTitle(result.sourceTitle);
            // Replace URL in search input with list title for display
            if (result.sourceTitle && searchQuery) {
              setSearchInput(result.sourceTitle);
            }
            // Store params for loadMore
            lastSearchParamsRef.current = {
              query: searchQuery,
              sort: sortOrder,
              fieldValues: effectiveFieldValues,
              providerOverride,
              contentType: effectiveContentType,
            };
          } else {
            setBooks([]);
            setHasMore(false);
            setTotalFound(0);
            setResultsSourceUrl(undefined);
            setResultsSourceTitle(undefined);
            showToast(t('No results found'), 'error');
          }
        } catch (error) {
          handleSearchError(error, 'Search failed');
        } finally {
          setIsSearching(false);
        }
        return;
      }

      // Direct mode: require a query
      if (!query) {
        setBooks([]);
        setLastSearchQuery('');
        return;
      }
      setIsSearching(true);
      setLastSearchQuery(query);

      try {
        const { books: results, totalResults } = await searchBooks(query);

        if (results.length > 0) {
          // When no explicit server-side sort is selected (empty or "downloads"),
          // sort locally by downloads descending so the most popular results
          // appear first.
          const effectiveSort = sort ?? advancedFilters.sort;
          const isDownloadsSort = !effectiveSort || effectiveSort === 'downloads';
          const sorted = isDownloadsSort ? sortBooksByDownloads(results) : results;
          setBooks(sorted);
          setDirectTotalResults(totalResults);
        } else {
          showToast(t('No results found'), 'error');
          setDirectTotalResults(null);
        }
      } catch (error) {
        if (error instanceof AuthenticationError) {
          handleSearchError(error, 'Search failed');
        } else {
          console.error('Search failed:', error);
          showToast(describeSearchFailure(error), 'error');
        }
      } finally {
        setIsSearching(false);
      }
    },
    [
      showToast,
      searchFieldValues,
      handleSearchError,
      contentType,
      advancedFilters,
      sortBooksByDownloads,
    ],
  );

  const handleResetSearch = useCallback(
    (config: AppConfig | null) => {
      setBooks([]);
      setSearchInput('');
      setShowAdvanced(false);
      setLastSearchQuery('');
      onSearchReset?.();

      const resetFormats = config?.supported_formats || DEFAULT_FORMAT_SELECTION;
      setAdvancedFilters({
        isbn: '',
        author: '',
        title: '',
        lang: [LANGUAGE_OPTION_DEFAULT],
        sort: '',
        content: '',
        formats: resetFormats,
      });

      // Reset universal mode search field values
      setSearchFieldValues({});
      setSearchFieldLabels({});

      // Reset pagination
      setCurrentPage(1);
      setHasMore(false);
      setTotalFound(0);
      setDirectTotalResults(null);
      setResultsSourceUrl(undefined);
      setResultsSourceTitle(undefined);
      lastSearchParamsRef.current = null;
    },
    [onSearchReset],
  );

  // Load more results (universal mode pagination)
  const loadMore = useCallback(
    async (config: AppConfig | null, searchModeOverride?: SearchMode) => {
      const searchMode = (searchModeOverride ?? config?.search_mode) || 'universal';
      if (searchMode !== 'universal') return;
      if (!lastSearchParamsRef.current) return;
      if (isLoadingMore || !hasMore) return;

      const {
        query,
        sort,
        fieldValues,
        providerOverride,
        contentType: searchContentType,
      } = lastSearchParamsRef.current;
      const nextPage = currentPage + 1;

      setIsLoadingMore(true);

      try {
        const result = await searchMetadata(
          query,
          40,
          sort,
          fieldValues,
          nextPage,
          searchContentType,
          providerOverride,
        );
        if (result.books.length > 0) {
          setBooks((prev) => [...prev, ...result.books]);
          setHasMore(result.hasMore);
          setCurrentPage(nextPage);
        } else {
          setHasMore(false);
        }
      } catch (error) {
        handleSearchError(error, 'Failed to load more results');
      } finally {
        setIsLoadingMore(false);
      }
    },
    [currentPage, hasMore, isLoadingMore, handleSearchError],
  );

  return {
    books,
    setBooks,
    isSearching,
    lastSearchQuery,
    searchInput,
    setSearchInput,
    showAdvanced,
    setShowAdvanced,
    advancedFilters,
    setAdvancedFilters,
    updateAdvancedFilters,
    handleSearch,
    handleResetSearch,
    resetSortFilter,
    reSortByDownloads,
    // Universal mode search field values
    searchFieldValues,
    updateSearchFieldValue,
    searchFieldLabels,
    // Pagination (universal mode only)
    hasMore,
    isLoadingMore,
    loadMore,
    totalFound,
    directTotalResults,
    resultsSourceUrl,
    resultsSourceTitle,
  };
}

import { useState, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Book, AppConfig, AdvancedFilterState } from '../types';
import { searchBooks, searchMetadata, AuthenticationError } from '../services/api';
import { LANGUAGE_OPTION_DEFAULT } from '../utils/languageFilters';
import { DEFAULT_SUPPORTED_FORMATS } from '../data/languages';

const DEFAULT_FORMAT_SELECTION = DEFAULT_SUPPORTED_FORMATS.filter(format => format !== 'pdf');

interface UseSearchOptions {
  showToast: (message: string, type: 'info' | 'success' | 'error') => void;
  setIsAuthenticated: (value: boolean) => void;
  authRequired: boolean;
  onSearchReset?: () => void;
}

// Search field values for universal mode (provider-specific fields)
type SearchFieldValues = Record<string, string | number | boolean>;

interface UseSearchReturn {
  books: Book[];
  setBooks: (books: Book[]) => void;
  isSearching: boolean;
  lastSearchQuery: string;
  searchInput: string;
  setSearchInput: (value: string) => void;
  showAdvanced: boolean;
  setShowAdvanced: (value: boolean) => void;
  advancedFilters: AdvancedFilterState;
  setAdvancedFilters: React.Dispatch<React.SetStateAction<AdvancedFilterState>>;
  updateAdvancedFilters: (updates: Partial<AdvancedFilterState>) => void;
  handleSearch: (query: string, config: AppConfig | null, fieldValues?: Record<string, string | number | boolean>) => Promise<void>;
  handleResetSearch: (config: AppConfig | null) => void;
  handleSortChange: (value: string, config: AppConfig | null) => void;
  resetSortFilter: () => void;
  // Universal mode search field values
  searchFieldValues: SearchFieldValues;
  updateSearchFieldValue: (key: string, value: string | number | boolean) => void;
  // Pagination (universal mode only)
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMore: (config: AppConfig | null) => Promise<void>;
  totalFound: number;
}

export function useSearch(options: UseSearchOptions): UseSearchReturn {
  const { showToast, setIsAuthenticated, authRequired, onSearchReset } = options;
  const navigate = useNavigate();

  const [books, setBooks] = useState<Book[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [lastSearchQuery, setLastSearchQuery] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [advancedFilters, setAdvancedFilters] = useState<AdvancedFilterState>({
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

  // Pagination state (universal mode only)
  const [currentPage, setCurrentPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [totalFound, setTotalFound] = useState(0);

  // Store last search params for loadMore
  const lastSearchParamsRef = useRef<{
    query: string;
    sort: string;
    fieldValues: SearchFieldValues;
  } | null>(null);

  const updateAdvancedFilters = useCallback((updates: Partial<AdvancedFilterState>) => {
    setAdvancedFilters(prev => ({ ...prev, ...updates }));
  }, []);

  const updateSearchFieldValue = useCallback((key: string, value: string | number | boolean) => {
    setSearchFieldValues(prev => ({ ...prev, [key]: value }));
  }, []);

  const resetSortFilter = useCallback(() => {
    setAdvancedFilters(prev => ({ ...prev, sort: '' }));
  }, []);

  // Helper to handle authentication and other errors consistently
  const handleSearchError = useCallback((error: unknown, context: string) => {
    if (error instanceof AuthenticationError) {
      setIsAuthenticated(false);
      if (authRequired) {
        navigate('/login', { replace: true });
      }
      return;
    }

    console.error(`${context}:`, error);
    const message = error instanceof Error ? error.message : context;
    showToast(message, 'error');
  }, [setIsAuthenticated, authRequired, navigate, showToast]);

  const handleSearch = useCallback(async (
    query: string,
    config: AppConfig | null,
    fieldValues?: Record<string, string | number | boolean>
  ) => {
    const searchMode = config?.search_mode || 'direct';

    // In universal mode, check if we have either a query or field values
    if (searchMode === 'universal') {
      const params = new URLSearchParams(query);
      const searchQuery = params.get('query') || '';
      // Use explicitly passed fieldValues if provided, otherwise fall back to state
      const effectiveFieldValues = fieldValues ?? searchFieldValues;
      const hasFieldValues = Object.values(effectiveFieldValues).some(v => v !== '' && v !== false);

      // Auto-set sort to series_order when searching by series field
      const seriesValue = effectiveFieldValues.series;
      const hasSeriesSearch = typeof seriesValue === 'string' && seriesValue.trim() !== '';
      const sort = hasSeriesSearch ? 'series_order' : (params.get('sort') || 'relevance');

      if (!searchQuery && !hasFieldValues) {
        setBooks([]);
        setLastSearchQuery('');
        setHasMore(false);
        setTotalFound(0);
        setCurrentPage(1);
        lastSearchParamsRef.current = null;
        return;
      }

      // Update UI sort dropdown to reflect series_order when searching by series
      if (hasSeriesSearch) {
        setAdvancedFilters(prev => ({ ...prev, sort: 'series_order' }));
      }

      setIsSearching(true);
      setLastSearchQuery(query);
      // Reset pagination for new search
      setCurrentPage(1);
      setHasMore(false);
      setTotalFound(0);

      try {
        const result = await searchMetadata(searchQuery, 40, sort, effectiveFieldValues, 1);
        if (result.books.length > 0) {
          setBooks(result.books);
          setHasMore(result.hasMore);
          setTotalFound(result.totalFound);
          // Store params for loadMore
          lastSearchParamsRef.current = { query: searchQuery, sort, fieldValues: effectiveFieldValues };
        } else {
          setBooks([]);
          setHasMore(false);
          setTotalFound(0);
          showToast('No results found', 'error');
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
      const results = await searchBooks(query);

      if (results.length > 0) {
        setBooks(results);
      } else {
        showToast('No results found', 'error');
      }
    } catch (error) {
      if (error instanceof AuthenticationError) {
        handleSearchError(error, 'Search failed');
      } else {
        console.error('Search failed:', error);
        const message = error instanceof Error ? error.message : 'Search failed';
        const friendly = message.includes("Anna's Archive") || message.includes('Network restricted')
          ? message
          : "Unable to reach Anna's Archive. Network may be restricted or mirrors blocked.";
        showToast(friendly, 'error');
      }
    } finally {
      setIsSearching(false);
    }
  }, [showToast, setIsAuthenticated, authRequired, navigate, searchFieldValues, handleSearchError]);

  const handleResetSearch = useCallback((config: AppConfig | null) => {
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

    // Reset pagination
    setCurrentPage(1);
    setHasMore(false);
    setTotalFound(0);
    lastSearchParamsRef.current = null;
  }, [onSearchReset]);

  // Load more results (universal mode pagination)
  const loadMore = useCallback(async (config: AppConfig | null) => {
    const searchMode = config?.search_mode || 'direct';
    if (searchMode !== 'universal') return;
    if (!lastSearchParamsRef.current) return;
    if (isLoadingMore || !hasMore) return;

    const { query, sort, fieldValues } = lastSearchParamsRef.current;
    const nextPage = currentPage + 1;

    setIsLoadingMore(true);

    try {
      const result = await searchMetadata(query, 40, sort, fieldValues, nextPage);
      if (result.books.length > 0) {
        setBooks(prev => [...prev, ...result.books]);
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
  }, [currentPage, hasMore, isLoadingMore, handleSearchError]);

  const handleSortChange = useCallback((value: string, config: AppConfig | null) => {
    updateAdvancedFilters({ sort: value });
    if (!lastSearchQuery) return;

    const params = new URLSearchParams(lastSearchQuery);
    if (value) {
      params.set('sort', value);
    } else {
      params.delete('sort');
    }

    const nextQuery = params.toString();
    if (!nextQuery) return;
    handleSearch(nextQuery, config);
  }, [lastSearchQuery, updateAdvancedFilters, handleSearch]);

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
    handleSortChange,
    resetSortFilter,
    // Universal mode search field values
    searchFieldValues,
    updateSearchFieldValue,
    // Pagination (universal mode only)
    hasMore,
    isLoadingMore,
    loadMore,
    totalFound,
  };
}

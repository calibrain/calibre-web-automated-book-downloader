import { useState, useCallback } from 'react';
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

  const updateAdvancedFilters = useCallback((updates: Partial<AdvancedFilterState>) => {
    setAdvancedFilters(prev => ({ ...prev, ...updates }));
  }, []);

  const updateSearchFieldValue = useCallback((key: string, value: string | number | boolean) => {
    console.log('[useSearch] updateSearchFieldValue:', key, '=', value);
    setSearchFieldValues(prev => {
      const next = { ...prev, [key]: value };
      console.log('[useSearch] searchFieldValues updated:', next);
      return next;
    });
  }, []);

  const resetSortFilter = useCallback(() => {
    setAdvancedFilters(prev => ({ ...prev, sort: '' }));
  }, []);

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

      // Debug logging
      console.log('[useSearch] Universal mode search:', {
        query,
        searchQuery,
        sort,
        searchFieldValues,
        fieldValues,
        effectiveFieldValues,
        hasFieldValues,
      });

      if (!searchQuery && !hasFieldValues) {
        console.log('[useSearch] Early return: no query and no field values');
        setBooks([]);
        setLastSearchQuery('');
        return;
      }

      // Update UI sort dropdown to reflect series_order when searching by series
      if (hasSeriesSearch) {
        setAdvancedFilters(prev => ({ ...prev, sort: 'series_order' }));
      }

      setIsSearching(true);
      setLastSearchQuery(query);

      try {
        const results = await searchMetadata(searchQuery, 20, sort, effectiveFieldValues);
        if (results.length > 0) {
          setBooks(results);
        } else {
          showToast('No results found', 'error');
        }
      } catch (error) {
        if (error instanceof AuthenticationError) {
          setIsAuthenticated(false);
          if (authRequired) {
            navigate('/login', { replace: true });
          }
        } else {
          console.error('Search failed:', error);
          // API now returns user-friendly error messages directly
          const message = error instanceof Error ? error.message : 'Search failed';
          showToast(message, 'error');
        }
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
        setIsAuthenticated(false);
        if (authRequired) {
          navigate('/login', { replace: true });
        }
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
  }, [showToast, setIsAuthenticated, authRequired, navigate, searchFieldValues]);

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
  }, [onSearchReset]);

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
  };
}

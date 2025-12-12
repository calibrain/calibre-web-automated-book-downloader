import { useState, useEffect, useCallback, useRef, CSSProperties } from 'react';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import {
  Book,
  StatusData,
  ButtonStateInfo,
  AppConfig,
  LoginCredentials,
  AdvancedFilterState,
} from './types';
import { searchBooks, getBookInfo, downloadBook, cancelDownload, clearCompleted, getConfig, login, logout, checkAuth, AuthenticationError } from './services/api';
import { useToast } from './hooks/useToast';
import { useRealtimeStatus } from './hooks/useRealtimeStatus';
import { Header } from './components/Header';
import { SearchSection } from './components/SearchSection';
import { AdvancedFilters } from './components/AdvancedFilters';
import { ResultsSection } from './components/ResultsSection';
import { DetailsModal } from './components/DetailsModal';
import { DownloadsSidebar } from './components/DownloadsSidebar';
import { ToastContainer } from './components/ToastContainer';
import { Footer } from './components/Footer';
import { LoginPage } from './pages/LoginPage';
import { DEFAULT_LANGUAGES, DEFAULT_SUPPORTED_FORMATS } from './data/languages';
import { LANGUAGE_OPTION_DEFAULT } from './utils/languageFilters';
import { buildSearchQuery } from './utils/buildSearchQuery';
import './styles.css';

const DEFAULT_FORMAT_SELECTION = DEFAULT_SUPPORTED_FORMATS.filter(format => format !== 'pdf');

function App() {
  // Authentication state
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(false);
  const [authRequired, setAuthRequired] = useState<boolean>(true);
  const [authChecked, setAuthChecked] = useState<boolean>(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState<boolean>(false);
  const navigate = useNavigate();
  
  const [books, setBooks] = useState<Book[]>([]);
  const [selectedBook, setSelectedBook] = useState<Book | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [downloadsSidebarOpen, setDownloadsSidebarOpen] = useState(false);
  const [lastSearchQuery, setLastSearchQuery] = useState('');
  const [advancedFilters, setAdvancedFilters] = useState<AdvancedFilterState>({
    isbn: '',
    author: '',
    title: '',
    lang: [LANGUAGE_OPTION_DEFAULT],
    sort: '',
    content: '',
    formats: DEFAULT_FORMAT_SELECTION,
  });
  const { toasts, showToast, removeToast } = useToast();
  const updateAdvancedFilters = useCallback((updates: Partial<AdvancedFilterState>) => {
    setAdvancedFilters(prev => ({ ...prev, ...updates }));
  }, []);
  
  // Determine WebSocket URL based on current location
  // In production, use the same origin as the page; in dev, use localhost
  const wsUrl = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'http://localhost:8084'
    : window.location.origin;
  
  // Use realtime status with WebSocket and polling fallback
  const { 
    status: currentStatus, 
    isUsingWebSocket,
    forceRefresh: fetchStatus 
  } = useRealtimeStatus({
    wsUrl,
    pollInterval: 5000,
    reconnectAttempts: 3,
  });
  
  // Calculate status counts for header badges
  const getStatusCounts = () => {
    const ongoing = [
      currentStatus.queued,
      currentStatus.resolving,
      currentStatus.downloading,
    ].reduce((sum, status) => sum + (status ? Object.keys(status).length : 0), 0);

    const completed = currentStatus.complete
      ? Object.keys(currentStatus.complete).length
      : 0;

    const errored = currentStatus.error ? Object.keys(currentStatus.error).length : 0;

    return { ongoing, completed, errored };
  };

  const statusCounts = getStatusCounts();
  const activeCount = statusCounts.ongoing;

  // Compute visibility states
  const hasResults = books.length > 0;
  const isInitialState = !hasResults;

  // Detect status changes and show notifications
  const detectChanges = useCallback((prev: StatusData, curr: StatusData) => {
    if (!prev || Object.keys(prev).length === 0) return;

    // Check for new items in queue
    const prevQueued = prev.queued || {};
    const currQueued = curr.queued || {};
    Object.keys(currQueued).forEach(bookId => {
      if (!prevQueued[bookId]) {
        const book = currQueued[bookId];
        showToast(`${book.title || 'Book'} added to queue`, 'info');
      }
    });

    // Check for items that started downloading
    const prevDownloading = prev.downloading || {};
    const currDownloading = curr.downloading || {};
    Object.keys(currDownloading).forEach(bookId => {
      if (!prevDownloading[bookId]) {
        const book = currDownloading[bookId];
        showToast(`${book.title || 'Book'} started downloading`, 'info');
      }
    });

    // Check for completed items
    const prevDownloadingIds = new Set(Object.keys(prevDownloading));
    const prevResolvingIds = new Set(Object.keys(prev.resolving || {}));
    const prevQueuedIds = new Set(Object.keys(prevQueued));
    const currComplete = curr.complete || {};

    Object.keys(currComplete).forEach(bookId => {
      if (prevDownloadingIds.has(bookId) || prevQueuedIds.has(bookId)) {
        const book = currComplete[bookId];
        showToast(`${book.title || 'Book'} completed`, 'success');
      }
    });

    // Check for failed items
    const currError = curr.error || {};
    Object.keys(currError).forEach(bookId => {
      if (prevDownloadingIds.has(bookId) || prevResolvingIds.has(bookId) || prevQueuedIds.has(bookId)) {
        const book = currError[bookId];
        const errorMsg = book.status_message || 'Download failed';
        showToast(`${book.title || 'Book'}: ${errorMsg}`, 'error');
      }
    });
  }, [showToast]);

  // Track previous status for change detection
  const prevStatusRef = useRef<StatusData>({});
  
  // Check authentication on mount
  useEffect(() => {
    const verifyAuth = async () => {
      try {
        const response = await checkAuth();
        const authenticated = response.authenticated || false;
        const authIsRequired = response.auth_required !== false; // Default to true if undefined
        
        setAuthRequired(authIsRequired);
        setIsAuthenticated(authenticated);
      } catch (error) {
        console.error('Auth check failed:', error);
        // On error, assume auth is required and user is not authenticated
        setAuthRequired(true);
        setIsAuthenticated(false);
      } finally {
        setAuthChecked(true);
      }
    };
    verifyAuth();
  }, []);

  // Authentication handlers
  const handleLogin = async (credentials: LoginCredentials) => {
    setIsLoggingIn(true);
    setLoginError(null);
    try {
      const response = await login(credentials);
      if (response.success) {
        setIsAuthenticated(true);
        setLoginError(null);
        navigate('/', { replace: true });
      } else {
        setLoginError(response.error || 'Login failed');
      }
    } catch (error) {
      if (error instanceof Error) {
        setLoginError(error.message || 'Login failed');
      } else {
        setLoginError('Login failed');
      }
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleLogout = async () => {
    try {
      await logout();
      setIsAuthenticated(false);
      // Clear application state
      setBooks([]);
      setSelectedBook(null);
      setSearchInput('');
      setLastSearchQuery('');
      navigate('/login', { replace: true });
    } catch (error) {
      console.error('Logout failed:', error);
      showToast('Logout failed', 'error');
    }
  };

  // Detect status changes when currentStatus updates
  useEffect(() => {
    if (prevStatusRef.current && Object.keys(prevStatusRef.current).length > 0) {
      detectChanges(prevStatusRef.current, currentStatus);
    }
    prevStatusRef.current = currentStatus;
  }, [currentStatus, detectChanges]);

  // Fetch config on mount and when authentication changes
  useEffect(() => {
    const loadConfig = async () => {
      try {
        const cfg = await getConfig();
        setConfig(cfg);
        // Update format selection to match supported formats from config
        // This ensures PDF is auto-selected when added to SUPPORTED_FORMATS env var
        if (cfg?.supported_formats) {
          setAdvancedFilters(prev => ({
            ...prev,
            formats: cfg.supported_formats,
          }));
        }
      } catch (error) {
        console.error('Failed to load config:', error);
        // Use defaults if config fails to load
      }
    };
    // Only fetch config if authenticated (or auth is not required)
    if (isAuthenticated) {
      loadConfig();
    }
  }, [isAuthenticated]);

  // Log WebSocket connection status changes
  useEffect(() => {
    if (isUsingWebSocket) {
      console.log('✅ Using WebSocket for real-time updates');
    } else {
      console.log('⏳ Using polling fallback (5s interval)');
    }
  }, [isUsingWebSocket]);

  // Fetch status immediately on startup
  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  // Search handler
  const handleSearch = async (query: string) => {
    if (!query) {
      setBooks([]);
      setLastSearchQuery('');
      return;
    }
    setIsSearching(true);
    setLastSearchQuery(query);
    try {
      const results = await searchBooks(query);
      setBooks(results);
      if (results.length === 0) {
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
        setBooks([]);
        const message = error instanceof Error ? error.message : 'Search failed';
        const friendly = message.includes("Anna's Archive") || message.includes('Network restricted')
          ? message
          : "Unable to reach Anna's Archive. Network may be restricted or mirrors blocked.";
        showToast(friendly, 'error');
      }
    } finally {
      setIsSearching(false);
    }
  };

  // Show book details
  const handleShowDetails = async (id: string): Promise<void> => {
    try {
      const book = await getBookInfo(id);
      setSelectedBook(book);
    } catch (error) {
      console.error('Failed to load book details:', error);
      showToast('Failed to load book details', 'error');
    }
  };

  // Download book
  const handleDownload = async (book: Book): Promise<void> => {
    try {
      await downloadBook(book.id);
      // Fetch status to update button states (detectChanges will show toast)
      await fetchStatus();
    } catch (error) {
      console.error('Download failed:', error);
      showToast('Failed to queue download', 'error');
    }
  };

  // Cancel download
  const handleCancel = async (id: string) => {
    try {
      await cancelDownload(id);
      await fetchStatus();
    } catch (error) {
      console.error('Cancel failed:', error);
    }
  };

  // Clear completed
  const handleClearCompleted = async () => {
    try {
      await clearCompleted();
      await fetchStatus();
    } catch (error) {
      console.error('Clear completed failed:', error);
    }
  };

  // Reset search state (clear books and search input)
  const handleResetSearch = () => {
    setBooks([]);
    setSearchInput('');
    setShowAdvanced(false);
    setLastSearchQuery('');
    // Use config's supported formats if available, otherwise fall back to default
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
  };

  const handleSortChange = (value: string) => {
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
    handleSearch(nextQuery);
  };

  // Get button state for a book - memoized to ensure proper re-renders when status changes
  const getButtonState = useCallback((bookId: string): ButtonStateInfo => {
    // Check error first
    if (currentStatus.error && currentStatus.error[bookId]) {
      return { text: 'Failed', state: 'error' };
    }
    // Check completed
    if (currentStatus.complete && currentStatus.complete[bookId]) {
      return { text: 'Downloaded', state: 'complete' };
    }
    // Check in-progress states
    if (currentStatus.downloading && currentStatus.downloading[bookId]) {
      const book = currentStatus.downloading[bookId];
      return {
        text: 'Downloading',
        state: 'downloading',
        progress: book.progress
      };
    }
    if (currentStatus.resolving && currentStatus.resolving[bookId]) {
      return { text: 'Resolving', state: 'resolving' };
    }
    if (currentStatus.queued && currentStatus.queued[bookId]) {
      return { text: 'Queued', state: 'queued' };
    }
    return { text: 'Download', state: 'download' };
  }, [currentStatus]);

  const bookLanguages = config?.book_languages || DEFAULT_LANGUAGES;
  const supportedFormats = config?.supported_formats || DEFAULT_SUPPORTED_FORMATS;
  const defaultLanguageCodes =
    config?.default_language && config.default_language.length > 0
      ? config.default_language
      : [bookLanguages[0]?.code || 'en'];

  const mainAppContent = (
    <>
      <Header 
        calibreWebUrl={config?.calibre_web_url || ''} 
        debug={config?.debug || false}
        logoUrl="/logo.png"
        showSearch={!isInitialState}
        searchInput={searchInput}
        onSearchChange={setSearchInput}
        onDownloadsClick={() => setDownloadsSidebarOpen(true)}
        statusCounts={statusCounts}
        onLogoClick={handleResetSearch}
        authRequired={authRequired}
        isAuthenticated={isAuthenticated}
        onLogout={handleLogout}
        onSearch={() => {
          const query = buildSearchQuery({
            searchInput,
            showAdvanced,
            advancedFilters,
            bookLanguages,
            defaultLanguage: defaultLanguageCodes,
          });
          handleSearch(query);
        }}
        onAdvancedToggle={() => setShowAdvanced(!showAdvanced)}
        isLoading={isSearching}
        onShowToast={showToast}
        onRemoveToast={removeToast}
      />
      
      <AdvancedFilters
        visible={showAdvanced && !isInitialState}
        bookLanguages={bookLanguages}
        defaultLanguage={defaultLanguageCodes}
        supportedFormats={supportedFormats}
        filters={advancedFilters}
        onFiltersChange={updateAdvancedFilters}
      />
      
      <main className="w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-3 sm:py-6">
        <SearchSection
          onSearch={handleSearch}
          isLoading={isSearching}
          isInitialState={isInitialState}
          bookLanguages={bookLanguages}
          defaultLanguage={defaultLanguageCodes}
          supportedFormats={config?.supported_formats || DEFAULT_SUPPORTED_FORMATS}
          logoUrl="/logo.png"
          searchInput={searchInput}
          onSearchInputChange={setSearchInput}
          showAdvanced={showAdvanced}
          onAdvancedToggle={() => setShowAdvanced(!showAdvanced)}
        advancedFilters={advancedFilters}
        onAdvancedFiltersChange={updateAdvancedFilters}
        />

        <ResultsSection
          books={books}
          visible={hasResults}
          onDetails={handleShowDetails}
          onDownload={handleDownload}
          getButtonState={getButtonState}
          sortValue={advancedFilters.sort}
          onSortChange={handleSortChange}
        />

        {selectedBook && (
          <DetailsModal
            book={selectedBook}
            onClose={() => setSelectedBook(null)}
            onDownload={handleDownload}
            buttonState={getButtonState(selectedBook.id)}
          />
        )}

      </main>

      <Footer 
        buildVersion={config?.build_version} 
        releaseVersion={config?.release_version} 
        debug={config?.debug}
      />
      <ToastContainer toasts={toasts} />
      
      {/* Downloads Sidebar */}
      <DownloadsSidebar
        isOpen={downloadsSidebarOpen}
        onClose={() => setDownloadsSidebarOpen(false)}
        status={currentStatus}
        onRefresh={fetchStatus}
        onClearCompleted={handleClearCompleted}
        onCancel={handleCancel}
        activeCount={activeCount}
      />
    </>
  );

  const visuallyHiddenStyle: CSSProperties = {
    position: 'absolute',
    width: '1px',
    height: '1px',
    padding: 0,
    margin: '-1px',
    overflow: 'hidden',
    clip: 'rect(0, 0, 0, 0)',
    whiteSpace: 'nowrap',
    border: 0,
  };

  if (!authChecked) {
    return (
      <div aria-live="polite" style={visuallyHiddenStyle}>
        Checking authentication…
      </div>
    );
  }

  const shouldRedirectFromLogin = !authRequired || isAuthenticated;
  const appElement = authRequired && !isAuthenticated ? (
    <Navigate to="/login" replace />
  ) : (
    mainAppContent
  );

  return (
    <Routes>
      <Route
        path="/login"
        element={
          shouldRedirectFromLogin ? (
            <Navigate to="/" replace />
          ) : (
            <LoginPage
              onLogin={handleLogin}
              error={loginError}
              isLoading={isLoggingIn}
            />
          )
        }
      />
      <Route path="/*" element={appElement} />
    </Routes>
  );
}

export default App;

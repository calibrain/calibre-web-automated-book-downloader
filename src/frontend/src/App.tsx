import { useState, useEffect, useCallback, useRef, useMemo, CSSProperties } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import {
  Book,
  Release,
  StatusData,
  AppConfig,
  ContentType,
} from './types';
import { getBookInfo, getMetadataBookInfo, downloadBook, downloadRelease, cancelDownload, clearCompleted, getConfig } from './services/api';
import { useToast } from './hooks/useToast';
import { useRealtimeStatus } from './hooks/useRealtimeStatus';
import { useAuth } from './hooks/useAuth';
import { useSearch } from './hooks/useSearch';
import { useUrlSearch } from './hooks/useUrlSearch';
import { useDownloadTracking } from './hooks/useDownloadTracking';
import { Header } from './components/Header';
import { SearchSection } from './components/SearchSection';
import { AdvancedFilters } from './components/AdvancedFilters';
import { ResultsSection } from './components/ResultsSection';
import { DetailsModal } from './components/DetailsModal';
import { ReleaseModal } from './components/ReleaseModal';
import { DownloadsSidebar } from './components/DownloadsSidebar';
import { ToastContainer } from './components/ToastContainer';
import { Footer } from './components/Footer';
import { LoginPage } from './pages/LoginPage';
import { SettingsModal } from './components/settings';
import { ConfigSetupBanner } from './components/ConfigSetupBanner';
import { DEFAULT_LANGUAGES, DEFAULT_SUPPORTED_FORMATS } from './data/languages';
import { buildSearchQuery } from './utils/buildSearchQuery';
import { SearchModeProvider } from './contexts/SearchModeContext';
import './styles.css';

function App() {
  const { toasts, showToast, removeToast } = useToast();

  // Realtime status with WebSocket and polling fallback
  // Socket connection is managed by SocketProvider in main.tsx
  const {
    status: currentStatus,
    isUsingWebSocket,
    forceRefresh: fetchStatus
  } = useRealtimeStatus({
    pollInterval: 5000,
  });

  // Download tracking for universal mode
  const {
    bookToReleaseMap,
    trackRelease,
    markBookCompleted,
    clearTracking,
    getButtonState,
    getUniversalButtonState,
  } = useDownloadTracking(currentStatus);

  // Authentication state and handlers
  // Initialized first since search hook needs auth state
  const {
    isAuthenticated,
    authRequired,
    authChecked,
    isAdmin,
    loginError,
    isLoggingIn,
    setIsAuthenticated,
    handleLogin,
    handleLogout,
  } = useAuth({
    showToast,
  });

  // Content type state (ebook vs audiobook) - defined before useSearch since it's passed to it
  const [contentType, setContentType] = useState<ContentType>('ebook');

  // Search state and handlers
  const {
    books,
    setBooks,
    isSearching,
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
    searchFieldValues,
    updateSearchFieldValue,
    // Pagination (universal mode)
    hasMore,
    isLoadingMore,
    loadMore,
    totalFound,
  } = useSearch({
    showToast,
    setIsAuthenticated,
    authRequired,
    onSearchReset: clearTracking,
    contentType,
  });

  // Wire up logout callback to clear search state
  const handleLogoutWithCleanup = useCallback(async () => {
    await handleLogout();
    setBooks([]);
    clearTracking();
  }, [handleLogout, setBooks, clearTracking]);

  // UI state
  const [selectedBook, setSelectedBook] = useState<Book | null>(null);
  const [releaseBook, setReleaseBook] = useState<Book | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [downloadsSidebarOpen, setDownloadsSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [configBannerOpen, setConfigBannerOpen] = useState(false);
  const [featureNoticeDismissed, setFeatureNoticeDismissed] = useState(() => {
    return localStorage.getItem('cwa-bd-prowlarr-irc-notice-dismissed') === 'true';
  });

  const handleDismissFeatureNotice = useCallback(() => {
    localStorage.setItem('cwa-bd-prowlarr-irc-notice-dismissed', 'true');
    setFeatureNoticeDismissed(true);
  }, []);

  // URL-based search: parse URL params for automatic search on page load
  const urlSearchEnabled = isAuthenticated && config !== null;
  const { parsedParams, wasProcessed } = useUrlSearch({ enabled: urlSearchEnabled });
  const urlSearchExecutedRef = useRef(false);

  // Track previous status and search mode for change detection
  const prevStatusRef = useRef<StatusData>({});
  const prevSearchModeRef = useRef<string | undefined>(undefined);

  // Calculate status counts for header badges (memoized)
  const statusCounts = useMemo(() => {
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
  }, [currentStatus]);


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
        // Auto-open downloads sidebar if enabled
        if (config?.auto_open_downloads_sidebar !== false) {
          setDownloadsSidebarOpen(true);
        }
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

        // Auto-download to browser if enabled
        if (config?.download_to_browser && book.download_path) {
          const link = document.createElement('a');
          link.href = `/api/localdownload?id=${encodeURIComponent(bookId)}`;
          link.download = '';
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
        }

        // Track completed release IDs in session state for universal mode
        Object.entries(bookToReleaseMap).forEach(([metadataBookId, releaseIds]) => {
          if (releaseIds.includes(bookId)) {
            markBookCompleted(metadataBookId);
          }
        });
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
  }, [showToast, bookToReleaseMap, markBookCompleted, config]);

  // Detect status changes when currentStatus updates
  useEffect(() => {
    if (prevStatusRef.current && Object.keys(prevStatusRef.current).length > 0) {
      detectChanges(prevStatusRef.current, currentStatus);
    }
    prevStatusRef.current = currentStatus;
  }, [currentStatus, detectChanges]);

  // Load config function
  const loadConfig = useCallback(async (mode: 'initial' | 'settings-saved' = 'initial') => {
    try {
      const cfg = await getConfig();

      // Check if search mode changed (only on settings save)
      if (mode === 'settings-saved' && prevSearchModeRef.current !== cfg.search_mode) {
        setBooks([]);
        setSelectedBook(null);
        clearTracking();
      }

      prevSearchModeRef.current = cfg.search_mode;
      setConfig(cfg);

      // Determine the default sort based on search mode
      const defaultSort = cfg.search_mode === 'universal'
        ? (cfg.metadata_default_sort || 'relevance')
        : (cfg.default_sort || 'relevance');

      if (cfg?.supported_formats) {
        if (mode === 'initial') {
          setAdvancedFilters(prev => ({
            ...prev,
            formats: cfg.supported_formats,
            sort: defaultSort,
          }));
        } else if (mode === 'settings-saved') {
          // On settings save, update formats and reset sort to new default
          setAdvancedFilters(prev => ({
            ...prev,
            formats: prev.formats.filter(f => cfg.supported_formats.includes(f)),
            sort: defaultSort,
          }));
        }
      }
    } catch (error) {
      console.error('Failed to load config:', error);
    }
  }, [setBooks, setAdvancedFilters, clearTracking]);

  // Fetch config when authenticated
  useEffect(() => {
    if (isAuthenticated) {
      loadConfig('initial');
    }
  }, [isAuthenticated, loadConfig]);

  // Execute URL-based search when params are present
  useEffect(() => {
    if (
      wasProcessed &&
      parsedParams?.hasSearchParams &&
      !urlSearchExecutedRef.current &&
      config
    ) {
      urlSearchExecutedRef.current = true;

      const searchMode = config.search_mode || 'direct';
      const bookLanguages = config.book_languages || [];
      const defaultLanguageCodes =
        config.default_language && config.default_language.length > 0
          ? config.default_language
          : [bookLanguages[0]?.code || 'en'];

      // Populate search input from URL
      if (parsedParams.searchInput) {
        setSearchInput(parsedParams.searchInput);
      }

      // Apply advanced filters from URL
      if (Object.keys(parsedParams.advancedFilters).length > 0) {
        setAdvancedFilters(prev => ({
          ...prev,
          ...parsedParams.advancedFilters,
        }));

        // Show advanced panel if we have filter values (not just query/sort)
        const hasAdvancedValues = ['isbn', 'author', 'title', 'content'].some(
          key => parsedParams.advancedFilters[key as keyof typeof parsedParams.advancedFilters]
        );
        if (hasAdvancedValues) {
          setShowAdvanced(true);
        }
      }

      // Build query and trigger search
      const mergedFilters = {
        ...advancedFilters,
        ...parsedParams.advancedFilters,
      };

      const query = buildSearchQuery({
        searchInput: parsedParams.searchInput,
        showAdvanced: true,
        advancedFilters: mergedFilters as typeof advancedFilters,
        bookLanguages,
        defaultLanguage: defaultLanguageCodes,
        searchMode,
      });

      handleSearch(query, config, searchFieldValues);
    }
  }, [
    wasProcessed,
    parsedParams,
    config,
    advancedFilters,
    searchFieldValues,
    handleSearch,
    setSearchInput,
    setAdvancedFilters,
    setShowAdvanced,
  ]);

  const handleSettingsSaved = useCallback(() => {
    loadConfig('settings-saved');
  }, [loadConfig]);

  // Log WebSocket connection status
  useEffect(() => {
    if (isUsingWebSocket) {
      console.log('✅ Using WebSocket for real-time updates');
    } else {
      console.log('⏳ Using polling fallback (5s interval)');
    }
  }, [isUsingWebSocket]);

  // Fetch status on startup
  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  // Show book details
  const handleShowDetails = async (id: string): Promise<void> => {
    const metadataBook = books.find(b => b.id === id && b.provider && b.provider_id);

    if (metadataBook) {
      try {
        const fullBook = await getMetadataBookInfo(metadataBook.provider!, metadataBook.provider_id!);
        setSelectedBook({
          ...metadataBook,
          description: fullBook.description || metadataBook.description,
          series_name: fullBook.series_name,
          series_position: fullBook.series_position,
          series_count: fullBook.series_count,
        });
      } catch (error) {
        console.error('Failed to load book description, using search data:', error);
        setSelectedBook(metadataBook);
      }
    } else {
      try {
        const book = await getBookInfo(id);
        setSelectedBook(book);
      } catch (error) {
        console.error('Failed to load book details:', error);
        showToast('Failed to load book details', 'error');
      }
    }
  };

  // Handle "Find Downloads" from DetailsModal
  const handleFindDownloads = (book: Book) => {
    setSelectedBook(null);
    setReleaseBook(book);
  };

  // Download book
  const handleDownload = async (book: Book): Promise<void> => {
    try {
      await downloadBook(book.id);
      await fetchStatus();
    } catch (error) {
      console.error('Download failed:', error);
      showToast('Failed to queue download', 'error');
      throw error;
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

  // Open release modal
  const handleGetReleases = async (book: Book) => {
    if (book.provider && book.provider_id) {
      try {
        const fullBook = await getMetadataBookInfo(book.provider, book.provider_id);
        setReleaseBook({
          ...book,
          description: fullBook.description || book.description,
          series_name: fullBook.series_name,
          series_position: fullBook.series_position,
          series_count: fullBook.series_count,
        });
      } catch (error) {
        console.error('Failed to load book description, using search data:', error);
        setReleaseBook(book);
      }
    } else {
      setReleaseBook(book);
    }
  };

  // Handle download from ReleaseModal
  const handleReleaseDownload = async (book: Book, release: Release, releaseContentType: ContentType) => {
    try {
      trackRelease(book.id, release.source_id);

      await downloadRelease({
        source: release.source,
        source_id: release.source_id,
        title: book.title,    // Use book metadata title, not release/torrent title
        author: book.author,  // Pass author from metadata
        year: book.year,      // Pass year from metadata
        format: release.format,
        size: release.size,
        size_bytes: release.size_bytes,
        download_url: release.download_url,
        protocol: release.protocol,
        indexer: release.indexer,
        seeders: release.seeders,
        extra: release.extra,
        preview: book.preview,  // Pass book cover from metadata
        content_type: releaseContentType,  // For audiobook directory routing
        series_name: book.series_name,
        series_position: book.series_position,
        subtitle: book.subtitle,
      });
      await fetchStatus();
    } catch (error) {
      console.error('Release download failed:', error);
      showToast('Failed to queue download', 'error');
      throw error;
    }
  };

  const bookLanguages = config?.book_languages || DEFAULT_LANGUAGES;
  const supportedFormats = config?.supported_formats || DEFAULT_SUPPORTED_FORMATS;
  const defaultLanguageCodes =
    config?.default_language && config.default_language.length > 0
      ? config.default_language
      : [bookLanguages[0]?.code || 'en'];

  const searchMode = config?.search_mode || 'direct';

  // Handle "View Series" - trigger search with series field and series order sort
  const handleSearchSeries = useCallback((seriesName: string) => {
    // Clear UI state
    setSearchInput('');
    setSelectedBook(null);
    setReleaseBook(null);
    clearTracking();

    // Set sort to series_order (but don't show advanced panel or persist series value)
    const newFilters = { ...advancedFilters, sort: 'series_order' };
    setAdvancedFilters(newFilters);

    // Trigger search with series field (passed directly, not persisted in UI)
    const query = buildSearchQuery({
      searchInput: '',
      showAdvanced: true,
      advancedFilters: newFilters,
      bookLanguages,
      defaultLanguage: defaultLanguageCodes,
      searchMode,
    });
    handleSearch(query, config, { ...searchFieldValues, series: seriesName });
  }, [setSearchInput, clearTracking, searchFieldValues, advancedFilters, setAdvancedFilters, bookLanguages, defaultLanguageCodes, searchMode, config, handleSearch]);

  const mainAppContent = (
    <SearchModeProvider searchMode={searchMode}>
      <Header
        calibreWebUrl={config?.calibre_web_url || ''}
        debug={config?.debug || false}
        logoUrl="/logo.png"
        showSearch={!isInitialState}
        searchInput={searchInput}
        onSearchChange={setSearchInput}
        onDownloadsClick={() => setDownloadsSidebarOpen(true)}
        onSettingsClick={isAdmin ? () => {
          if (config?.settings_enabled) {
            setSettingsOpen(true);
          } else {
            setConfigBannerOpen(true);
          }
        } : undefined}
        statusCounts={statusCounts}
        onLogoClick={() => handleResetSearch(config)}
        authRequired={authRequired}
        isAuthenticated={isAuthenticated}
        onLogout={handleLogoutWithCleanup}
        onSearch={() => {
          const query = buildSearchQuery({
            searchInput,
            showAdvanced,
            advancedFilters,
            bookLanguages,
            defaultLanguage: defaultLanguageCodes,
            searchMode,
          });
          handleSearch(query, config, searchFieldValues);
        }}
        onAdvancedToggle={() => setShowAdvanced(!showAdvanced)}
        isLoading={isSearching}
        onShowToast={showToast}
        onRemoveToast={removeToast}
        contentType={contentType}
        onContentTypeChange={setContentType}
      />

      <AdvancedFilters
        visible={showAdvanced && !isInitialState}
        bookLanguages={bookLanguages}
        defaultLanguage={defaultLanguageCodes}
        supportedFormats={supportedFormats}
        filters={advancedFilters}
        onFiltersChange={updateAdvancedFilters}
        metadataSearchFields={config?.metadata_search_fields}
        searchFieldValues={searchFieldValues}
        onSearchFieldChange={updateSearchFieldValue}
        onSubmit={() => {
          const query = buildSearchQuery({
            searchInput,
            showAdvanced,
            advancedFilters,
            bookLanguages,
            defaultLanguage: defaultLanguageCodes,
            searchMode,
          });
          handleSearch(query, config, searchFieldValues);
        }}
      />

      <main className="relative w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-3 sm:py-6">
        <SearchSection
          onSearch={(query) => handleSearch(query, config, searchFieldValues)}
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
          metadataSearchFields={config?.metadata_search_fields}
          searchFieldValues={searchFieldValues}
          onSearchFieldChange={updateSearchFieldValue}
          contentType={contentType}
          onContentTypeChange={setContentType}
        />

        {isInitialState && !featureNoticeDismissed && (
          <div className="absolute bottom-4 left-0 right-0 text-center text-sm text-gray-500 dark:text-gray-400">
            <span>We've added Prowlarr and IRC support for more download options. Check Settings to configure.</span>
            <button
              onClick={handleDismissFeatureNotice}
              className="ml-2 text-blue-500 hover:text-blue-600 dark:text-blue-400 dark:hover:text-blue-300 underline"
            >
              Dismiss
            </button>
          </div>
        )}

        <ResultsSection
          books={books}
          visible={hasResults}
          onDetails={handleShowDetails}
          onDownload={handleDownload}
          onGetReleases={handleGetReleases}
          getButtonState={getButtonState}
          getUniversalButtonState={getUniversalButtonState}
          sortValue={advancedFilters.sort}
          onSortChange={(value) => handleSortChange(value, config)}
          metadataSortOptions={config?.metadata_sort_options}
          hasMore={hasMore}
          isLoadingMore={isLoadingMore}
          onLoadMore={() => loadMore(config)}
          totalFound={totalFound}
        />

        {selectedBook && (
          <DetailsModal
            book={selectedBook}
            onClose={() => setSelectedBook(null)}
            onDownload={handleDownload}
            onFindDownloads={handleFindDownloads}
            onSearchSeries={handleSearchSeries}
            buttonState={getButtonState(selectedBook.id)}
          />
        )}

        {releaseBook && (
          <ReleaseModal
            book={releaseBook}
            onClose={() => setReleaseBook(null)}
            onDownload={handleReleaseDownload}
            supportedFormats={supportedFormats}
            supportedAudiobookFormats={config?.supported_audiobook_formats || []}
            contentType={contentType}
            defaultLanguages={defaultLanguageCodes}
            bookLanguages={bookLanguages}
            currentStatus={currentStatus}
            defaultReleaseSource={config?.default_release_source}
            onSearchSeries={handleSearchSeries}
          />
        )}

      </main>

      <Footer
        buildVersion={config?.build_version}
        releaseVersion={config?.release_version}
        debug={config?.debug}
      />
      <ToastContainer toasts={toasts} />

      <DownloadsSidebar
        isOpen={downloadsSidebarOpen}
        onClose={() => setDownloadsSidebarOpen(false)}
        status={currentStatus}
        onClearCompleted={handleClearCompleted}
        onCancel={handleCancel}
      />

      <SettingsModal
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onShowToast={showToast}
        onSettingsSaved={handleSettingsSaved}
      />

      {/* Auto-show banner on startup for users without config */}
      {config && (
        <ConfigSetupBanner settingsEnabled={config.settings_enabled} />
      )}

      {/* Controlled banner shown when clicking settings without config */}
      <ConfigSetupBanner
        isOpen={configBannerOpen}
        onClose={() => setConfigBannerOpen(false)}
        onContinue={() => {
          setConfigBannerOpen(false);
          setSettingsOpen(true);
        }}
      />
    </SearchModeProvider>
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

  // Wait for config to load before rendering main UI to prevent flicker
  if (isAuthenticated && !config) {
    return (
      <div aria-live="polite" style={visuallyHiddenStyle}>
        Loading configuration…
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

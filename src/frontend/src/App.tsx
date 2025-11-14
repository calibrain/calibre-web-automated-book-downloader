import { useState, useEffect, useCallback, useRef } from 'react';
import { Book, StatusData, ButtonStateInfo, AppConfig } from './types';
import { searchBooks, getBookInfo, downloadBook, cancelDownload, clearCompleted, getConfig } from './services/api';
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
import { DEFAULT_LANGUAGES, DEFAULT_SUPPORTED_FORMATS } from './data/languages';
import './styles.css';

function App() {
  const [books, setBooks] = useState<Book[]>([]);
  const [selectedBook, setSelectedBook] = useState<Book | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [searchInput, setSearchInput] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [downloadsSidebarOpen, setDownloadsSidebarOpen] = useState(false);
  const [advancedFilters, setAdvancedFilters] = useState({
    isbn: '',
    author: '',
    title: '',
    lang: 'all',
    sort: '',
    content: '',
    formats: [] as string[],
  });
  const { toasts, showToast } = useToast();
  
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
      currentStatus.bypassing,
      currentStatus.downloading,
      currentStatus.verifying,
      currentStatus.ingesting,
    ].reduce((sum, status) => sum + (status ? Object.keys(status).length : 0), 0);

    const completed = [
      currentStatus.completed,
      currentStatus.complete,
      currentStatus.available,
      currentStatus.done,
    ].reduce((sum, status) => sum + (status ? Object.keys(status).length : 0), 0);

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
    const prevQueuedIds = new Set(Object.keys(prevQueued));
    const currAvailable = curr.available || {};
    const currDone = curr.done || {};

    Object.keys(currAvailable).forEach(bookId => {
      if (prevDownloadingIds.has(bookId) || prevQueuedIds.has(bookId)) {
        const book = currAvailable[bookId];
        showToast(`${book.title || 'Book'} completed`, 'success');
      }
    });

    Object.keys(currDone).forEach(bookId => {
      if (prevDownloadingIds.has(bookId) || prevQueuedIds.has(bookId)) {
        const book = currDone[bookId];
        showToast(`${book.title || 'Book'} completed`, 'success');
      }
    });
  }, [showToast]);

  // Track previous status for change detection
  const prevStatusRef = useRef<StatusData>({});
  
  // Detect status changes when currentStatus updates
  useEffect(() => {
    if (prevStatusRef.current && Object.keys(prevStatusRef.current).length > 0) {
      detectChanges(prevStatusRef.current, currentStatus);
    }
    prevStatusRef.current = currentStatus;
  }, [currentStatus, detectChanges]);

  // Fetch config on mount
  useEffect(() => {
    const loadConfig = async () => {
      try {
        const cfg = await getConfig();
        setConfig(cfg);
      } catch (error) {
        console.error('Failed to load config:', error);
        // Use defaults if config fails to load
      }
    };
    loadConfig();
  }, []);

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
      return;
    }
    setIsSearching(true);
    try {
      const results = await searchBooks(query);
      setBooks(results);
    } catch (error) {
      console.error('Search failed:', error);
      setBooks([]);
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
    setAdvancedFilters({
      isbn: '',
      author: '',
      title: '',
      lang: 'all',
      sort: '',
      content: '',
      formats: [],
    });
  };

  // Get button state for a book - memoized to ensure proper re-renders when status changes
  const getButtonState = useCallback((bookId: string): ButtonStateInfo => {
    // Check error first
    if (currentStatus.error && currentStatus.error[bookId]) {
      return { text: 'Failed', state: 'error' };
    }
    // Check completed states
    if (currentStatus.completed && currentStatus.completed[bookId]) {
      return { text: 'Downloaded', state: 'completed' };
    }
    if (currentStatus.complete && currentStatus.complete[bookId]) {
      return { text: 'Downloaded', state: 'completed' };
    }
    if (currentStatus.available && currentStatus.available[bookId]) {
      return { text: 'Downloaded', state: 'completed' };
    }
    if (currentStatus.done && currentStatus.done[bookId]) {
      return { text: 'Downloaded', state: 'completed' };
    }
    // Check in-progress states with detailed status
    if (currentStatus.ingesting && currentStatus.ingesting[bookId]) {
      return { text: 'Ingesting', state: 'ingesting' };
    }
    if (currentStatus.verifying && currentStatus.verifying[bookId]) {
      return { text: 'Verifying', state: 'verifying' };
    }
    if (currentStatus.downloading && currentStatus.downloading[bookId]) {
      const book = currentStatus.downloading[bookId];
      return {
        text: 'Downloading',
        state: 'downloading',
        progress: book.progress
      };
    }
    if (currentStatus.bypassing && currentStatus.bypassing[bookId]) {
      return { text: 'Bypassing Cloudflare...', state: 'bypassing' };
    }
    if (currentStatus.resolving && currentStatus.resolving[bookId]) {
      return { text: 'Resolving', state: 'resolving' };
    }
    if (currentStatus.queued && currentStatus.queued[bookId]) {
      return { text: 'Queued', state: 'queued' };
    }
    return { text: 'Download', state: 'download' };
  }, [currentStatus]);

  return (
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
        onSearch={() => {
          const q: string[] = [];
          const basic = searchInput.trim();
          if (basic) q.push(`query=${encodeURIComponent(basic)}`);
          
          if (showAdvanced) {
            if (advancedFilters.isbn) q.push(`isbn=${encodeURIComponent(advancedFilters.isbn)}`);
            if (advancedFilters.author) q.push(`author=${encodeURIComponent(advancedFilters.author)}`);
            if (advancedFilters.title) q.push(`title=${encodeURIComponent(advancedFilters.title)}`);
            if (advancedFilters.lang && advancedFilters.lang !== 'all') q.push(`lang=${encodeURIComponent(advancedFilters.lang)}`);
            if (advancedFilters.sort) q.push(`sort=${encodeURIComponent(advancedFilters.sort)}`);
            if (advancedFilters.content) q.push(`content=${encodeURIComponent(advancedFilters.content)}`);
            advancedFilters.formats.forEach(f => q.push(`format=${encodeURIComponent(f)}`));
          }
          
          handleSearch(q.join('&'));
        }}
        onAdvancedToggle={() => setShowAdvanced(!showAdvanced)}
        isLoading={isSearching}
      />
      
      <AdvancedFilters
        visible={showAdvanced && !isInitialState}
        bookLanguages={config?.book_languages || DEFAULT_LANGUAGES}
        defaultLanguage={config?.default_language || 'en'}
        supportedFormats={config?.supported_formats || DEFAULT_SUPPORTED_FORMATS}
        onFiltersChange={setAdvancedFilters}
      />
      
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <SearchSection
          onSearch={handleSearch}
          isLoading={isSearching}
          isInitialState={isInitialState}
          bookLanguages={config?.book_languages || DEFAULT_LANGUAGES}
          defaultLanguage={config?.default_language || 'en'}
          supportedFormats={config?.supported_formats || DEFAULT_SUPPORTED_FORMATS}
          logoUrl="/logo.png"
          searchInput={searchInput}
          onSearchInputChange={setSearchInput}
          showAdvanced={showAdvanced}
          onAdvancedToggle={() => setShowAdvanced(!showAdvanced)}
        />

        <ResultsSection
          books={books}
          visible={hasResults}
          onDetails={handleShowDetails}
          onDownload={handleDownload}
          getButtonState={getButtonState}
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
        buildVersion={config?.build_version || 'dev'} 
        releaseVersion={config?.release_version || 'dev'} 
        appEnv={config?.app_env || 'development'} 
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
}

export default App;

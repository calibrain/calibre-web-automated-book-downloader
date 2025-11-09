import { useState, useEffect, useCallback, useRef } from 'react';
import { Book, StatusData, ButtonStateInfo, AppConfig } from './types';
import { searchBooks, getBookInfo, downloadBook, cancelDownload, clearCompleted, getConfig } from './services/api';
import { useToast } from './hooks/useToast';
import { useRealtimeStatus } from './hooks/useRealtimeStatus';
import { Header } from './components/Header';
import { SearchSection } from './components/SearchSection';
import { ActiveDownloadsSection } from './components/ActiveDownloadsSection';
import { ResultsSection } from './components/ResultsSection';
import { DetailsModal } from './components/DetailsModal';
import { StatusSection } from './components/StatusSection';
import { ToastContainer } from './components/ToastContainer';
import { Footer } from './components/Footer';
import { DEFAULT_LANGUAGES, DEFAULT_SUPPORTED_FORMATS } from './data/languages';
import './styles.css';

function App() {
  const [books, setBooks] = useState<Book[]>([]);
  const [selectedBook, setSelectedBook] = useState<Book | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [isNearBottom, setIsNearBottom] = useState(false);
  const [isPageScrollable, setIsPageScrollable] = useState(false);
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
  
  // Calculate active count from status
  const activeCount = currentStatus.downloading
    ? Object.keys(currentStatus.downloading).length
    : 0;

  // Compute visibility states
  const hasResults = books.length > 0;
  const hasActiveDownloads =
    currentStatus.downloading && Object.keys(currentStatus.downloading).length > 0;
  const hasStatusItems = Object.values(currentStatus).some(
    section => section && Object.keys(section).length > 0
  );
  const isInitialState = !hasResults && !hasActiveDownloads && !hasStatusItems;

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

  // Get button state for a book
  const getButtonState = (bookId: string): ButtonStateInfo => {
    if (currentStatus.downloading && currentStatus.downloading[bookId]) {
      return { text: 'Downloading', state: 'downloading' };
    }
    if (currentStatus.queued && currentStatus.queued[bookId]) {
      return { text: 'Queued', state: 'queued' };
    }
    return { text: 'Download', state: 'download' };
  };

  // Get active downloads list
  const activeDownloads = currentStatus.downloading
    ? Object.values(currentStatus.downloading)
    : [];

  // Track scroll position and page scrollability
  useEffect(() => {
    const handleScroll = () => {
      const scrollPosition = window.scrollY + window.innerHeight;
      const documentHeight = document.documentElement.scrollHeight;
      const windowHeight = window.innerHeight;
      
      // Check if page is scrollable (content height > viewport height)
      setIsPageScrollable(documentHeight > windowHeight);
      
      // Consider "near bottom" if within 300px of the bottom
      setIsNearBottom(scrollPosition >= documentHeight - 300);
    };

    window.addEventListener('scroll', handleScroll);
    window.addEventListener('resize', handleScroll); // Also check on resize
    handleScroll(); // Check initial position
    return () => {
      window.removeEventListener('scroll', handleScroll);
      window.removeEventListener('resize', handleScroll);
    };
  }, []);

  // Scroll to downloads or back to top
  const handleFABClick = () => {
    if (isNearBottom) {
      // Scroll to top (search section)
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } else {
      // Scroll to downloads section
      const statusSection = document.getElementById('status-section');
      if (statusSection) {
        statusSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  };

  // Show FAB when there are downloads to show AND the page is scrollable
  const showDownloadsFAB = (hasActiveDownloads || hasStatusItems) && isPageScrollable;

  return (
    <>
      <Header 
        calibreWebUrl={config?.calibre_web_url || ''} 
        debug={config?.debug || false} 
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
        />

        <ActiveDownloadsSection
          downloads={activeDownloads}
          visible={!!hasActiveDownloads}
          onRefresh={fetchStatus}
          onCancel={handleCancel}
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

        <StatusSection
          status={currentStatus}
          visible={hasStatusItems}
          activeCount={activeCount}
          onRefresh={fetchStatus}
          onClearCompleted={handleClearCompleted}
          onCancel={handleCancel}
        />
      </main>

      <Footer 
        buildVersion={config?.build_version || 'dev'} 
        releaseVersion={config?.release_version || 'dev'} 
        appEnv={config?.app_env || 'development'} 
      />
      <ToastContainer toasts={toasts} />
      
      {/* Floating action button to scroll to downloads or back to top */}
      {showDownloadsFAB && (
        <button
          onClick={handleFABClick}
          className="fixed bottom-6 right-6 bg-blue-600 hover:bg-blue-700 text-white rounded-full p-4 shadow-lg transition-all duration-200 hover:scale-110 z-40"
          aria-label={isNearBottom ? 'Back to top' : 'View downloads'}
        >
          {isNearBottom ? (
            // Up arrow
            <svg
              className="w-6 h-6"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="2.5"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M4.5 15.75l7.5-7.5 7.5 7.5"
              />
            </svg>
          ) : (
            // Down arrow
            <svg
              className="w-6 h-6"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="2.5"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M19.5 8.25l-7.5 7.5-7.5-7.5"
              />
            </svg>
          )}
          {!isNearBottom && activeCount > 0 && (
            <span className="absolute -top-1 -right-1 bg-red-500 text-white text-xs font-bold rounded-full w-6 h-6 flex items-center justify-center">
              {activeCount}
            </span>
          )}
        </button>
      )}
    </>
  );
}

export default App;

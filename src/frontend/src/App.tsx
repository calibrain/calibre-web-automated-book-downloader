import { useState, useEffect, useCallback } from 'react';
import { Book, StatusData, ButtonStateInfo, AppConfig } from './types';
import { searchBooks, getBookInfo, downloadBook, getStatus, cancelDownload, clearCompleted, getConfig } from './services/api';
import { useToast } from './hooks/useToast';
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
  const [currentStatus, setCurrentStatus] = useState<StatusData>({});
  const [activeCount, setActiveCount] = useState(0);
  const [selectedBook, setSelectedBook] = useState<Book | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const { toasts, showToast } = useToast();

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

  // Fetch status
  const fetchStatus = useCallback(async () => {
    try {
      const data = await getStatus();
      setCurrentStatus(prevStatus => {
        // Detect changes and show toasts using previous state
        detectChanges(prevStatus, data);
        return data;
      });
      
      // Update active count
      const downloading = data.downloading || {};
      setActiveCount(Object.keys(downloading).length);
    } catch (error) {
      console.error('Failed to fetch status:', error);
    }
  }, [detectChanges]);

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

  // Auto-refresh status every 5 seconds
  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
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
      fetchStatus();
    } catch (error) {
      console.error('Cancel failed:', error);
    }
  };

  // Clear completed
  const handleClearCompleted = async () => {
    try {
      await clearCompleted();
      fetchStatus();
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
    </>
  );
}

export default App;

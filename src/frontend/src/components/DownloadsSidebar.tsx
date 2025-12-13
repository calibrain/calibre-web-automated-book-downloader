import { useEffect } from 'react';
import { StatusData, Book } from '../types';

interface DownloadsSidebarProps {
  isOpen: boolean;
  onClose: () => void;
  status: StatusData;
  onRefresh: () => void;
  onClearCompleted: () => void;
  onCancel: (id: string) => void;
  activeCount: number;
}

const STATUS_STYLES: Record<string, { bg: string; text: string; label: string; waveColor: string }> = {
  queued: { bg: 'bg-amber-500/20', text: 'text-amber-700 dark:text-amber-300', label: 'Queued', waveColor: 'rgba(217, 119, 6, 0.3)' },
  resolving: { bg: 'bg-indigo-500/20', text: 'text-indigo-700 dark:text-indigo-300', label: 'Resolving', waveColor: 'rgba(79, 70, 229, 0.3)' },
  downloading: { bg: 'bg-sky-500/20', text: 'text-sky-700 dark:text-sky-300', label: 'Downloading', waveColor: 'rgba(2, 132, 199, 0.3)' },
  complete: { bg: 'bg-green-500/20', text: 'text-green-700 dark:text-green-300', label: 'Complete', waveColor: '' },
  error: { bg: 'bg-red-500/20', text: 'text-red-700 dark:text-red-300', label: 'Error', waveColor: '' },
  cancelled: { bg: 'bg-gray-500/20', text: 'text-gray-700 dark:text-gray-300', label: 'Cancelled', waveColor: '' },
};

// Helper to format file size with uppercase units
const formatSize = (sizeStr?: string): string => {
  if (!sizeStr) return '';
  // Convert lowercase units to uppercase (kb -> KB, mb -> MB, etc.)
  return sizeStr.replace(/\b(kb|mb|gb|tb)\b/gi, (match) => match.toUpperCase());
};

// Add keyframe animation for wave effect
const styleSheet = document.createElement('style');
styleSheet.textContent = `
  @keyframes wave {
    0% { background-position: -200% 0; }
    100% { background-position: 200% 0; }
  }
`;
if (!document.head.querySelector('style[data-wave-animation]')) {
  styleSheet.setAttribute('data-wave-animation', 'true');
  document.head.appendChild(styleSheet);
}

// Helper to get book preview image
const getBookPreview = (book: Book): string => {
  return book.preview || '/placeholder-book.png';
};

// Helper to get progress percentage based on status
const getStatusProgress = (statusName: string, bookProgress?: number): number => {
  switch (statusName) {
    case 'queued':
      return 5;
    case 'resolving':
      return 15;
    case 'downloading':
      // Map actual progress (0-100) to 20-100 range
      if (typeof bookProgress === 'number') {
        return 20 + (bookProgress * 0.8);
      }
      return 20;
    case 'complete':
    case 'error':
      return 100;
    default:
      return 0;
  }
};

// Helper to get progress bar color based on status
const getProgressBarColor = (statusName: string): string => {
  if (statusName === 'complete') return 'bg-green-600';
  if (statusName === 'error') return 'bg-red-600';
  if (statusName === 'queued') return 'bg-amber-600';
  if (statusName === 'resolving') return 'bg-indigo-600';
  if (statusName === 'downloading') return 'bg-sky-600';
  return 'bg-sky-600';
};


export const DownloadsSidebar = ({
  isOpen,
  onClose,
  status,
  onRefresh,
  onClearCompleted,
  onCancel,
  activeCount,
}: DownloadsSidebarProps) => {
  // Handle ESC key to close sidebar
  useEffect(() => {
    if (!isOpen) return; // Only listen when sidebar is open
    
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [isOpen, onClose]);

  // Collect all download items from different status sections
  const allDownloadItems: Array<{ book: Book; status: string }> = [];

  const statusTypes = ['downloading', 'resolving', 'queued', 'error', 'complete', 'cancelled'];

  statusTypes.forEach((statusName) => {
    const items = (status as any)[statusName];
    if (items && Object.keys(items).length > 0) {
      Object.values(items).forEach((book: any) => {
        allDownloadItems.push({ book, status: statusName });
      });
    }
  });

  // Sort by added_time descending (newest first)
  allDownloadItems.sort((a, b) => (b.book.added_time || 0) - (a.book.added_time || 0));

  const renderDownloadItem = (item: { book: Book; status: string }) => {
    const { book, status: statusName } = item;
    const statusStyle = STATUS_STYLES[statusName] || {
      bg: 'bg-gray-500/10',
      text: 'text-gray-600',
      label: statusName.charAt(0).toUpperCase() + statusName.slice(1),
    };

    const isInProgress = ['queued', 'resolving', 'downloading'].includes(statusName);
    const isCompleted = statusName === 'complete';
    const hasError = statusName === 'error';
    
    // Get progress information
    const progress = getStatusProgress(statusName, book.progress);
    const progressBarColor = getProgressBarColor(statusName);
    
    // Format progress text - use status_message if available, otherwise fall back to label
    let progressText = book.status_message || statusStyle.label;
    if (statusName === 'downloading' && book.progress && book.size) {
      const downloadedMB = (book.progress / 100) * parseFloat(book.size.replace(/[^\d.]/g, ''));
      progressText = `${downloadedMB.toFixed(1)}mb / ${book.size}`;
    } else if (isCompleted) {
      progressText = 'Complete';
    } else if (hasError) {
      progressText = book.status_message || 'Failed';
    }

    return (
      <div
        key={book.id}
        className="relative rounded-lg border hover:shadow-md transition-shadow overflow-hidden"
        style={{ borderColor: 'var(--border-muted)', background: 'var(--bg-soft)' }}
      >
        {/* Cancel/Clear Button - top right corner */}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onCancel(book.id);
          }}
          className="absolute top-1 right-1 z-10 flex items-center justify-center w-6 h-6 rounded-full hover:bg-red-100 dark:hover:bg-red-900/30 text-gray-500 hover:text-red-600 transition-colors"
          title={isInProgress ? "Cancel download" : "Clear from list"}
          aria-label={isInProgress ? "Cancel download" : "Clear from list"}
        >
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        {/* Main content area */}
        <div className="flex gap-2">
          {/* Book Thumbnail - left side */}
          <div className="flex-shrink-0">
            <img
              src={getBookPreview(book)}
              alt={book.title || 'Book cover'}
              className="w-16 h-24 object-cover rounded shadow-sm"
              style={{ aspectRatio: '2/3' }}
              onError={(e) => {
                const target = e.target as HTMLImageElement;
                target.src = '/placeholder-book.png';
              }}
            />
          </div>

          {/* Book Info - right side */}
          <div className="flex-1 min-w-0 flex flex-col justify-between px-3 pt-2 pb-3">
            {/* Title & Author - with safe area for cancel/clear button */}
            <div className="mb-1 pr-6">
              <h3 className="font-semibold text-sm truncate" title={book.title}>
                {isCompleted && book.download_path ? (
                  <a
                    href={`/api/localdownload?id=${encodeURIComponent(book.id)}`}
                    className="text-sky-600 hover:underline"
                  >
                    {book.title || 'Unknown Title'}
                  </a>
                ) : (
                  book.title || 'Unknown Title'
                )}
              </h3>
              <p className="text-xs opacity-70 truncate" title={book.author}>
                {book.author || 'Unknown Author'}
              </p>
            </div>

            {/* Details Row */}
            <div className="space-y-1 pb-8">
              <div className="flex items-center gap-2">
                {/* Format and Size */}
                <div className="text-xs opacity-70">
                  {book.format && <span className="uppercase">{book.format}</span>}
                  {book.format && book.size && <span> • </span>}
                  {book.size && <span>{formatSize(book.size)}</span>}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Progress Bar - absolute positioned at bottom - always visible */}
        <div className="absolute bottom-0 left-0 right-0 pointer-events-none">
          <div className="flex justify-end p-2">
            <span
              className={`relative inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${statusStyle.bg} ${statusStyle.text}`}
            >
              {/* Wave animation overlay for in-progress states */}
              {isInProgress && statusStyle.waveColor && (
                <span
                  key={statusName}
                  className="absolute inset-0"
                  style={{
                    background: `linear-gradient(90deg, transparent 0%, ${statusStyle.waveColor} 50%, transparent 100%)`,
                    backgroundSize: '200% 100%',
                    animation: 'wave 2s linear infinite',
                  }}
                />
              )}
              <span className="relative">{progressText}</span>
            </span>
          </div>
          <div className="h-1.5 bg-gray-200 dark:bg-gray-700 overflow-hidden relative">
            <div
              className={`h-full ${progressBarColor} transition-all duration-300 relative overflow-hidden`}
              style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
            >
              {/* Animated wave effect for in-progress states */}
              {isInProgress && progress < 100 && (
                <div
                  className="absolute inset-0 opacity-30"
                  style={{
                    background: 'linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.5) 50%, transparent 100%)',
                    backgroundSize: '200% 100%',
                    animation: 'wave 2s ease-in-out infinite',
                  }}
                />
              )}
            </div>
          </div>
        </div>
      </div>
    );
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 bg-black/50 z-40 transition-opacity duration-300 ${
          isOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
        onClick={onClose}
      />

      {/* Sidebar */}
      <div
        className={`fixed top-0 right-0 h-full w-full sm:w-96 z-50 flex flex-col shadow-2xl transition-transform duration-300 ${
          isOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
        style={{ background: 'var(--bg)' }}
      >
        {/* Header */}
        <div
          className="flex items-center justify-between p-4"
          style={{ paddingTop: 'calc(1rem + env(safe-area-inset-top))' }}
        >
          <h2 className="text-lg font-semibold">Downloads</h2>
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 w-10 items-center justify-center rounded-full hover-action transition-colors"
            aria-label="Close sidebar"
          >
            <svg
              className="w-5 h-5"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="2"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Controls */}
        <div
          className="flex items-center gap-2 p-4 border-b"
          style={{ borderColor: 'var(--border-muted)' }}
        >
          <button
            type="button"
            onClick={onClearCompleted}
            className="flex-1 flex items-center justify-center px-3 py-2 h-10 rounded border text-sm hover-action transition-colors"
            style={{ borderColor: 'var(--border-muted)' }}
          >
            Clear Completed
          </button>
          <button
            type="button"
            onClick={onRefresh}
            className="flex items-center justify-center h-10 w-10 rounded-full text-sm hover-action transition-colors ml-auto"
            aria-label="Refresh"
            title="Refresh"
          >
            <svg
              className="w-4 h-4"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="2"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"
              />
            </svg>
          </button>
        </div>

        {/* Queue Items */}
        <div
          className="flex-1 overflow-y-auto p-4 space-y-3"
          style={{ paddingBottom: 'calc(1rem + env(safe-area-inset-bottom))' }}
        >
          {allDownloadItems.length > 0 ? (
            allDownloadItems.map((item) => renderDownloadItem(item))
          ) : (
            <div className="text-center text-sm opacity-70 mt-8">
              No downloads in queue
            </div>
          )}
        </div>

        {/* Footer with active count */}
        {activeCount > 0 && (
          <div
            className="p-3 border-t text-xs text-center opacity-70"
            style={{
              borderColor: 'var(--border-muted)',
              paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom))',
            }}
          >
            {activeCount} active {activeCount === 1 ? 'download' : 'downloads'}
          </div>
        )}
      </div>
    </>
  );
};

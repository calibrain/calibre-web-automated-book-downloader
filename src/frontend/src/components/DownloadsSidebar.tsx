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

const STATUS_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  queued: { bg: 'bg-amber-500/10', text: 'text-amber-600', label: 'Queued' },
  resolving: { bg: 'bg-indigo-500/10', text: 'text-indigo-600', label: 'Resolving' },
  bypassing: { bg: 'bg-purple-500/10', text: 'text-purple-600', label: 'Bypassing Cloudflare...' },
  downloading: { bg: 'bg-blue-500/10', text: 'text-blue-600', label: 'Downloading' },
  verifying: { bg: 'bg-cyan-500/10', text: 'text-cyan-600', label: 'Verifying' },
  ingesting: { bg: 'bg-teal-500/10', text: 'text-teal-600', label: 'Ingesting' },
  complete: { bg: 'bg-green-500/10', text: 'text-green-600', label: 'Complete' },
  completed: { bg: 'bg-green-500/10', text: 'text-green-600', label: 'Completed' },
  available: { bg: 'bg-green-500/10', text: 'text-green-600', label: 'Available' },
  done: { bg: 'bg-green-500/10', text: 'text-green-600', label: 'Done' },
  error: { bg: 'bg-red-500/10', text: 'text-red-600', label: 'Error' },
  cancelled: { bg: 'bg-gray-500/10', text: 'text-gray-600', label: 'Cancelled' },
};

// Helper to format file size
const formatSize = (sizeStr?: string): string => {
  if (!sizeStr) return '';
  return sizeStr;
};

// Helper to get book preview image
const getBookPreview = (book: Book): string => {
  return book.preview || '/placeholder-book.png';
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
  // Collect all download items from different status sections
  const allDownloadItems: Array<{ book: Book; status: string; order: number }> = [];
  
  // Priority order for display
  const statusOrder = ['downloading', 'bypassing', 'resolving', 'queued', 'verifying', 'ingesting', 'error', 'completed', 'complete', 'available', 'done', 'cancelled'];
  
  statusOrder.forEach((statusName, index) => {
    const items = (status as any)[statusName];
    if (items && Object.keys(items).length > 0) {
      Object.values(items).forEach((book: any) => {
        allDownloadItems.push({ book, status: statusName, order: index });
      });
    }
  });

  // Sort by status priority
  allDownloadItems.sort((a, b) => a.order - b.order);

  const renderDownloadItem = (item: { book: Book; status: string }) => {
    const { book, status: statusName } = item;
    const statusStyle = STATUS_STYLES[statusName] || {
      bg: 'bg-gray-500/10',
      text: 'text-gray-600',
      label: statusName.charAt(0).toUpperCase() + statusName.slice(1),
    };

    const isInProgress = ['queued', 'resolving', 'bypassing', 'downloading', 'verifying', 'ingesting'].includes(statusName);
    const isCompleted = ['completed', 'complete', 'available', 'done'].includes(statusName);
    const hasError = statusName === 'error';
    
    // Get progress information
    const progress = book.progress || 0;
    const hasProgress = typeof book.progress === 'number' && statusName === 'downloading';

    // Format progress text
    let progressText = statusStyle.label;
    if (hasProgress && book.size) {
      const downloadedMB = (progress / 100) * parseFloat(book.size.replace(/[^\d.]/g, ''));
      progressText = `${downloadedMB.toFixed(1)}MB / ${book.size}`;
    }

    return (
      <div
        key={book.id}
        className="p-3 rounded-lg border hover:shadow-md transition-shadow"
        style={{ borderColor: 'var(--border-muted)', background: 'var(--bg-soft)' }}
      >
        <div className="flex gap-3">
          {/* Book Thumbnail */}
          <div className="flex-shrink-0">
            <img
              src={getBookPreview(book)}
              alt={book.title || 'Book cover'}
              className="w-12 h-16 object-cover rounded shadow-sm"
              onError={(e) => {
                const target = e.target as HTMLImageElement;
                target.src = '/placeholder-book.png';
              }}
            />
          </div>

          {/* Book Info */}
          <div className="flex-1 min-w-0">
            {/* Title & Author */}
            <div className="mb-1">
              <h3 className="font-semibold text-sm truncate" title={book.title}>
                {isCompleted && book.download_path ? (
                  <a
                    href={`/request/api/localdownload?id=${encodeURIComponent(book.id)}`}
                    className="text-blue-600 hover:underline"
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

            {/* Status Badge */}
            <div className="mb-2">
              <span
                className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${statusStyle.bg} ${statusStyle.text}`}
              >
                {statusStyle.label}
              </span>
            </div>

            {/* Progress Bar */}
            {hasProgress && (
              <div className="mb-2">
                <div className="h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-600 transition-all duration-300"
                    style={{ width: `${Math.min(100, Math.max(0, progress))}%` }}
                  />
                </div>
                <p className="text-xs opacity-70 mt-1">{progressText}</p>
              </div>
            )}

            {/* Details & Actions Row */}
            <div className="flex items-center justify-between gap-2">
              <div className="text-xs opacity-70">
                {book.format && <span className="uppercase">{book.format}</span>}
                {book.format && book.size && <span> • </span>}
                {book.size && <span>{formatSize(book.size)}</span>}
              </div>

              {/* Cancel Button for in-progress items */}
              {isInProgress && (
                <button
                  onClick={() => onCancel(book.id)}
                  className="text-xs px-2 py-1 rounded border hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                  style={{ borderColor: 'var(--border-muted)' }}
                  title="Cancel download"
                >
                  ✕
                </button>
              )}
            </div>

            {/* Error Message */}
            {hasError && (
              <p className="text-xs text-red-600 mt-1">Download failed</p>
            )}
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
          className="flex items-center justify-between p-4 border-b"
          style={{ borderColor: 'var(--border-muted)' }}
        >
          <h2 className="text-lg font-semibold">Downloads</h2>
          <button
            onClick={onClose}
            className="p-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
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
            onClick={onClearCompleted}
            className="flex-1 px-3 py-2 rounded border text-sm hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            style={{ borderColor: 'var(--border-muted)' }}
          >
            Clear Completed
          </button>
          <button
            onClick={onRefresh}
            className="px-3 py-2 rounded border text-sm hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
            style={{ borderColor: 'var(--border-muted)' }}
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
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
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
            style={{ borderColor: 'var(--border-muted)' }}
          >
            {activeCount} active {activeCount === 1 ? 'download' : 'downloads'}
          </div>
        )}
      </div>
    </>
  );
};

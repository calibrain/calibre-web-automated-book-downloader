import { useState, useEffect } from 'react';
import { Book, ButtonStateInfo } from '../types';
import { CircularProgress } from './CircularProgress';

interface DetailsModalProps {
  book: Book | null;
  onClose: () => void;
  onDownload: (book: Book) => Promise<void>;
  buttonState: ButtonStateInfo;
}

export const DetailsModal = ({ book, onClose, onDownload, buttonState }: DetailsModalProps) => {
  const [isQueuing, setIsQueuing] = useState(false);

  // Clear queuing state and close modal once button state changes from download
  useEffect(() => {
    if (isQueuing && buttonState.state !== 'download') {
      setIsQueuing(false);
      // Close modal after status has updated
      const timer = setTimeout(onClose, 500);
      return () => clearTimeout(timer);
    }
  }, [buttonState.state, isQueuing, onClose]);

  // Handle ESC key to close modal
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [onClose]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  if (!book) return null;

  const titleId = `book-details-title-${book.id}`;

  const isCompleted = buttonState.state === 'completed';
  const hasError = buttonState.state === 'error';
  const isInProgress = ['queued', 'resolving', 'bypassing', 'downloading', 'verifying', 'ingesting'].includes(buttonState.state);
  const isDisabled = buttonState.state !== 'download' || isQueuing || isCompleted;
  const displayText = isQueuing ? 'Queuing...' : buttonState.text;

  // Show circular progress only for downloading state with progress data
  const showCircularProgress = buttonState.state === 'downloading' && buttonState.progress !== undefined;
  // Show spinner for other in-progress states or when queuing
  const showSpinner = (isInProgress && !showCircularProgress) || isQueuing;

  const handleDownload = async () => {
    setIsQueuing(true);
    try {
      await onDownload(book);
      // Don't close here - wait for button state to change
    } catch (error) {
      setIsQueuing(false);
      // Close on error
      setTimeout(onClose, 300);
    }
  };

  const publisherInfo = { label: 'Publisher', value: book.publisher || '-' };
  const metadata = [
    { label: 'Year', value: book.year || '-' },
    { label: 'Language', value: book.language || '-' },
    { label: 'Format', value: book.format || '-' },
    { label: 'Size', value: book.size || '-' },
  ];
  const artworkMaxHeight = 'calc(90vh - 220px)';
  const artworkMaxWidth = 'min(45vw, 520px, calc((90vh - 220px) / 1.6))';
  const additionalInfo =
    book.info && Object.keys(book.info).length > 0
      ? Object.entries(book.info).filter(([key]) => {
          const normalized = key.toLowerCase();
          return normalized !== 'language' && normalized !== 'year';
        })
      : [];

  return (
    <div
      className="modal-overlay active px-4 py-6 sm:px-6"
      onClick={e => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="details-container w-full max-w-4xl animate-fade-in-up"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex max-h-[90vh] flex-col overflow-hidden rounded-2xl border border-[var(--border-muted)] bg-[var(--bg-soft)] text-[var(--text)] shadow-2xl">
          <header className="flex items-start gap-4 border-b border-[var(--border-muted)] px-5 py-4">
            <div className="flex-1 space-y-1">
              <p className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Book</p>
              <h3 id={titleId} className="text-lg font-semibold leading-snug">
                {book.title || 'Untitled'}
              </h3>
              <p className="text-sm text-gray-600 dark:text-gray-300">
                {book.author || 'Unknown author'}
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded-full p-2 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900 dark:hover:bg-gray-700 dark:hover:text-gray-100"
              aria-label="Close details"
            >
              <svg
                className="h-5 w-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                strokeWidth={1.5}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </header>

          <div className="flex-1 min-h-0 overflow-y-auto px-5 py-6">
            <div className="flex flex-col gap-6 lg:flex-row lg:items-stretch lg:gap-8 lg:min-h-0">
              <div className="flex w-full justify-center lg:w-auto lg:flex-none lg:justify-start lg:self-stretch lg:pr-4">
                {book.preview ? (
                  <div
                    className="flex w-full items-center justify-center lg:h-full lg:max-w-none"
                    style={{ maxHeight: artworkMaxHeight, maxWidth: artworkMaxWidth }}
                  >
                    <img
                      src={book.preview}
                      alt="Book cover"
                      className="h-auto max-h-full w-auto max-w-full rounded-xl object-contain shadow-lg"
                      style={{ maxHeight: '100%', maxWidth: '100%' }}
                    />
                  </div>
                ) : (
                  <div
                    className="flex w-full items-center justify-center rounded-xl border border-dashed border-[var(--border-muted)] bg-[var(--bg)]/60 p-6 text-sm text-gray-500 lg:h-full lg:max-w-none"
                    style={{ maxHeight: artworkMaxHeight, maxWidth: artworkMaxWidth }}
                  >
                    No cover
                  </div>
                )}
              </div>

              <div className="flex flex-1 flex-col gap-4 sm:gap-5 lg:min-h-0">
                <div className="space-y-4 text-sm">
                  <div
                    className="rounded-2xl border border-[var(--border-muted)] px-4 py-3"
                    style={{ background: 'var(--bg)' }}
                  >
                    <p className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      {publisherInfo.label}
                    </p>
                    <p className="font-medium text-gray-900 dark:text-gray-100">{publisherInfo.value}</p>
                  </div>
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-2 lg:grid-cols-4 lg:gap-4">
                    {metadata.map(item => (
                      <div
                        key={item.label}
                        className="rounded-2xl border border-[var(--border-muted)] px-4 py-3"
                        style={{ background: 'var(--bg)' }}
                      >
                        <p className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
                          {item.label}
                        </p>
                        <p className="font-medium text-gray-900 dark:text-gray-100">{item.value}</p>
                      </div>
                    ))}
                  </div>
                </div>

                {additionalInfo.length > 0 && (
                  <section
                    className="space-y-3 rounded-2xl border border-[var(--border-muted)] px-4 py-4"
                    style={{ background: 'var(--bg)' }}
                  >
                    <h4 className="text-sm font-semibold">Further Information</h4>
                    <ul className="space-y-2 text-sm">
                      {additionalInfo.map(([key, value]) => (
                        <li key={key} className="flex flex-col gap-1">
                          <span className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
                            {key}
                          </span>
                          <span className="text-gray-900 dark:text-gray-100">
                            {Array.isArray(value) ? value.join(', ') : value}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </section>
                )}
              </div>
            </div>
          </div>

          <footer className="border-t border-[var(--border-muted)] bg-[var(--bg-soft)] px-5 py-4">
            <div className="flex justify-end">
              <button
                id="download-button"
                data-id={book.id}
                type="button"
                className={`inline-flex items-center justify-center gap-2 rounded-full px-4 py-3 text-sm font-medium text-white transition-colors ${
                  isCompleted
                    ? 'bg-green-600'
                    : hasError
                    ? 'bg-red-600'
                    : isInProgress || isQueuing
                    ? 'bg-gray-500'
                    : 'bg-sky-700 hover:bg-sky-800'
                } ${isDisabled ? 'cursor-not-allowed opacity-75' : ''}`}
                onClick={handleDownload}
                disabled={isDisabled || isInProgress}
              >
                <span className="download-button-text">{displayText}</span>
                {isCompleted && (
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                )}
                {hasError && (
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                )}
                {showCircularProgress && <CircularProgress progress={buttonState.progress} size={16} />}
                {showSpinner && (
                  <div className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
                )}
              </button>
            </div>
          </footer>
        </div>
      </div>
    </div>
  );
};

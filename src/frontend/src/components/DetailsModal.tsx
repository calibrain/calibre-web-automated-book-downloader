import { useState, useEffect, useCallback } from 'react';
import { Book, ButtonStateInfo, isMetadataBook } from '../types';
import { BookDownloadButton } from './BookDownloadButton';

interface DetailsModalProps {
  book: Book | null;
  onClose: () => void;
  onDownload: (book: Book) => Promise<void>;
  onFindDownloads?: (book: Book) => void;  // For Universal mode
  onSearchSeries?: (seriesName: string) => void;  // Callback to search for series
  buttonState: ButtonStateInfo;
}

export const DetailsModal = ({ book, onClose, onDownload, onFindDownloads, onSearchSeries, buttonState }: DetailsModalProps) => {
  const [isQueuing, setIsQueuing] = useState(false);
  const [isClosing, setIsClosing] = useState(false);

  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
    }, 150);
  }, [onClose]);

  // Clear queuing state and close modal once button state changes from download
  useEffect(() => {
    if (isQueuing && buttonState.state !== 'download') {
      setIsQueuing(false);
      // Close modal after status has updated
      const timer = setTimeout(handleClose, 500);
      return () => clearTimeout(timer);
    }
  }, [buttonState.state, isQueuing, handleClose]);

  // Handle ESC key to close modal
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleClose();
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [handleClose]);

  useEffect(() => {
    if (book) {
      const previousOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = previousOverflow;
      };
    }
  }, [book]);

  if (!book && !isClosing) return null;
  if (!book) return null;

  const titleId = `book-details-title-${book.id}`;

  const handleDownload = async () => {
    setIsQueuing(true);
    try {
      await onDownload(book);
      // Don't close here - wait for button state to change
    } catch (error) {
      setIsQueuing(false);
      // Close on error
      setTimeout(handleClose, 300);
    }
  };

  // Determine if this is a metadata book (Universal mode) vs a release (Direct Download)
  const isMetadata = isMetadataBook(book);

  const publisherInfo = { label: 'Publisher', value: book.publisher || '-' };

  // Build metadata grid based on mode
  // Universal mode: Year, Genres (no language, no publisher - often blank from providers)
  // Direct Download mode: Year, Language, Format, Size
  const metadata = isMetadata
    ? [
        { label: 'Year', value: book.year || '-' },
        ...(book.genres && book.genres.length > 0
          ? [{ label: 'Genres', value: book.genres.slice(0, 3).join(', ') }]
          : []),
      ]
    : [
        { label: 'Year', value: book.year || '-' },
        { label: 'Language', value: book.language || '-' },
        { label: 'Format', value: book.format || '-' },
        { label: 'Size', value: book.size || '-' },
      ];

  // Extract rating and readers from display_fields for dedicated boxes (Universal mode)
  const ratingField = isMetadata && book.display_fields?.find(f => f.icon === 'star');
  const readersField = isMetadata && book.display_fields?.find(f => f.icon === 'users');
  // Other display fields (pages, editions, etc.) shown inline
  const otherDisplayFields = isMetadata && book.display_fields?.filter(f => f.icon !== 'star' && f.icon !== 'users');

  // Use provider display name from backend, fall back to capitalized provider name
  const providerDisplay = book.provider_display_name
    || (book.provider ? book.provider.charAt(0).toUpperCase() + book.provider.slice(1) : '');
  const artworkMaxHeight = 'calc(90vh - 220px)';
  const artworkMaxWidth = 'min(45vw, 520px, calc((90vh - 220px) / 1.6))';
  const additionalInfo =
    book.info && Object.keys(book.info).length > 0
      ? Object.entries(book.info).filter(([key]) => {
          const normalized = key.toLowerCase();
          return normalized !== 'language' && normalized !== 'year';
        })
      : [];
  const extendedInfoEntries = [[publisherInfo.label, publisherInfo.value], ...additionalInfo];
  const infoCardClass = 'rounded-2xl border border-[var(--border-muted)] px-4 py-3 text-sm';
  const infoCardStyle = { background: 'var(--bg)' };
  const infoLabelClass = 'text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400';
  const infoValueClass = 'text-gray-900 dark:text-gray-100';

  return (
    <div
      className="modal-overlay active px-4 py-6 sm:px-6"
      onClick={e => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div
        className={`details-container w-full max-w-4xl ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
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
              onClick={handleClose}
              className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
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
                {book.description && (
                  <div className={`${infoCardClass} space-y-1`} style={infoCardStyle}>
                    <p className={infoLabelClass}>Description</p>
                    <p className={`${infoValueClass} whitespace-pre-line`}>{book.description}</p>
                  </div>
                )}

                {/* Metadata grid - adapts columns based on mode and available data */}
                <div className={`grid grid-cols-2 gap-3 lg:gap-4 ${isMetadata ? 'lg:grid-cols-2' : 'lg:grid-cols-4'}`}>
                  {metadata.map(item => (
                    <div key={item.label} className={`${infoCardClass} space-y-1`} style={infoCardStyle}>
                      <p className={infoLabelClass}>{item.label}</p>
                      <p className={infoValueClass}>{item.value}</p>
                    </div>
                  ))}

                  {/* Rating box - Universal mode only */}
                  {ratingField && (
                    <div className={`${infoCardClass} space-y-1`} style={infoCardStyle}>
                      <p className={infoLabelClass}>{ratingField.label}</p>
                      <p className={`${infoValueClass} flex items-center gap-1.5`}>
                        <svg className="h-4 w-4 text-amber-500" fill="currentColor" viewBox="0 0 20 20">
                          <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                        </svg>
                        {ratingField.value}
                      </p>
                    </div>
                  )}

                  {/* Readers box - Universal mode only */}
                  {readersField && (
                    <div className={`${infoCardClass} space-y-1`} style={infoCardStyle}>
                      <p className={infoLabelClass}>{readersField.label}</p>
                      <p className={`${infoValueClass} flex items-center gap-1.5`}>
                        <svg className="h-4 w-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" />
                        </svg>
                        {readersField.value}
                      </p>
                    </div>
                  )}
                </div>

                {/* Other display fields (pages, editions) - Universal mode only */}
                {otherDisplayFields && otherDisplayFields.length > 0 && (
                  <div className="flex flex-wrap gap-4 text-sm">
                    {otherDisplayFields.map(field => (
                      <span key={field.label} className="flex items-center gap-1.5">
                        {field.icon === 'book' && (
                          <svg className="h-4 w-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25" />
                          </svg>
                        )}
                        {field.icon === 'editions' && (
                          <svg className="h-4 w-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 6.878V6a2.25 2.25 0 0 1 2.25-2.25h7.5A2.25 2.25 0 0 1 18 6v.878m-12 0c.235-.083.487-.128.75-.128h10.5c.263 0 .515.045.75.128m-12 0A2.25 2.25 0 0 0 4.5 9v.878m13.5-3A2.25 2.25 0 0 1 19.5 9v.878m0 0a2.246 2.246 0 0 0-.75-.128H5.25c-.263 0-.515.045-.75.128m15 0A2.25 2.25 0 0 1 21 12v6a2.25 2.25 0 0 1-2.25 2.25H5.25A2.25 2.25 0 0 1 3 18v-6c0-.98.626-1.813 1.5-2.122" />
                          </svg>
                        )}
                        <span className="text-gray-500 dark:text-gray-400">{field.label}:</span>
                        <span className="text-gray-900 dark:text-gray-100">{field.value}</span>
                      </span>
                    ))}
                  </div>
                )}

                {/* ISBN - Universal mode only */}
                {isMetadata && (book.isbn_13 || book.isbn_10) && (
                  <div className={`${infoCardClass} space-y-1`} style={infoCardStyle}>
                    <p className={infoLabelClass}>ISBN</p>
                    <p className={infoValueClass}>{book.isbn_13 || book.isbn_10}</p>
                  </div>
                )}

                {/* Series info - Universal mode only */}
                {isMetadata && book.series_name && (
                  <div className={`${infoCardClass} space-y-1`} style={infoCardStyle}>
                    <p className={infoLabelClass}>Series</p>
                    <div className="flex items-center justify-between gap-2">
                      <p className={infoValueClass}>
                        {book.series_position != null ? (
                          <>#{Number.isInteger(book.series_position) ? book.series_position : book.series_position}{book.series_count ? ` of ${book.series_count}` : ''} in {book.series_name}</>
                        ) : (
                          book.series_name
                        )}
                      </p>
                      {onSearchSeries && (
                        <button
                          type="button"
                          onClick={() => {
                            onSearchSeries(book.series_name!);
                            handleClose();
                          }}
                          className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 rounded-full hover:bg-emerald-100 dark:hover:bg-emerald-900/40 transition-colors flex-shrink-0"
                        >
                          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
                          </svg>
                          View series
                        </button>
                      )}
                    </div>
                  </div>
                )}

                {/* Extended info (publisher, etc.) - Direct Download mode only */}
                {!isMetadata && extendedInfoEntries.length > 0 && (
                  <div className={`${infoCardClass} space-y-3`} style={infoCardStyle}>
                    <ul className="space-y-3 list-none">
                      {extendedInfoEntries.map(([key, value]) => (
                        <li key={key} className="space-y-1">
                          <p className={infoLabelClass}>{key}</p>
                          <p className={infoValueClass}>{Array.isArray(value) ? value.join(', ') : value}</p>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          </div>

          <footer className="border-t border-[var(--border-muted)] bg-[var(--bg-soft)] px-5 py-4">
            <div className="flex items-center justify-between gap-4">
              {/* Source link - Universal mode only */}
              {isMetadata && book.source_url ? (
                <a
                  href={book.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border-muted)] bg-[var(--bg)] px-3 py-2 text-xs font-medium text-gray-600 transition-colors hover:border-gray-400 hover:text-gray-900 dark:text-gray-400 dark:hover:border-gray-500 dark:hover:text-gray-200"
                >
                  View on {providerDisplay}
                  <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                    />
                  </svg>
                </a>
              ) : (
                <div />
              )}
              {isMetadata ? (
                <button
                  onClick={() => onFindDownloads?.(book)}
                  className="rounded-full bg-emerald-600 px-6 py-2.5 text-sm font-medium text-white transition-colors hover:bg-emerald-700 focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-offset-2"
                >
                  Find Downloads
                </button>
              ) : (
                <BookDownloadButton
                  buttonState={buttonState}
                  onDownload={handleDownload}
                  size="md"
                  className="rounded-full px-6 py-2.5 text-sm font-medium"
                  ariaLabel={`Download ${book.title || 'book'}`}
                />
              )}
            </div>
          </footer>
        </div>
      </div>
    </div>
  );
};

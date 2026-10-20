import { useCallback, useState } from 'react';
import { createPortal } from 'react-dom';

import { useBodyScrollLock } from '../hooks/useBodyScrollLock';
import { useEscapeKey } from '../hooks/useEscapeKey';
import { useMountEffect } from '../hooks/useMountEffect';
import type { Book, ButtonStateInfo } from '../types';
import { isMetadataBook } from '../types';
import { bookSupportsTargets } from '../utils/bookTargetLoader';
import { isUserCancelledError } from '../utils/errors';
import { BookTargetDropdown } from './BookTargetDropdown';
import { LibraryBadge, isInLibrary } from './shared';

interface DetailsModalProps {
  book: Book | null;
  onClose: () => void;
  onDownload: (book: Book) => Promise<void>;
  onFindDownloads?: (book: Book) => void; // For Universal mode
  onSearchSeries?: (seriesName: string, seriesId?: string) => void; // Callback to search for series
  buttonState: ButtonStateInfo;
  showReleaseSourceLinks?: boolean;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

interface DetailsModalAutoCloseProps {
  clearQueuing: () => void;
  closeModal: () => void;
}

const DetailsModalAutoClose = ({ clearQueuing, closeModal }: DetailsModalAutoCloseProps) => {
  useMountEffect(() => {
    clearQueuing();
    const timer = window.setTimeout(closeModal, 500);
    return () => window.clearTimeout(timer);
  });

  return null;
};

export const DetailsModal = ({
  book,
  onClose,
  onDownload,
  onFindDownloads,
  onSearchSeries,
  buttonState,
  showReleaseSourceLinks = true,
  onShowToast,
}: DetailsModalProps) => {
  const [isQueuing, setIsQueuing] = useState(false);
  const [isClosing, setIsClosing] = useState(false);

  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
    }, 150);
  }, [onClose]);

  useBodyScrollLock(Boolean(book));
  useEscapeKey(Boolean(book), handleClose);

  const hasBookTargets = Boolean(book && isMetadataBook(book) && bookSupportsTargets(book));

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
      if (isUserCancelledError(error)) {
        return;
      }
      // Close on error
      setTimeout(handleClose, 300);
    }
  };

  // Determine if this is a metadata book (Universal mode) vs a release (Direct Download)
  const isMetadata = isMetadataBook(book);
  const showBookSourceLink = Boolean(book.source_url) && (isMetadata || showReleaseSourceLinks);
  const metadataActionText =
    isMetadata && buttonState.state === 'download' && buttonState.text === 'Get'
      ? 'Find Downloads'
      : buttonState.text;
  const downloadButtonClassName = (() => {
    if (buttonState.state === 'blocked') {
      return 'bg-gray-500';
    }
    return isMetadata ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-sky-700 hover:bg-sky-800';
  })();
  const publisherInfo = { label: 'Publisher', value: book.publisher || '-' };
  const bookProvider = book.provider;
  const bookProviderId = book.provider_id;

  // Build metadata grid based on mode
  // Universal mode: Year, Genres (no language, no publisher - often blank from providers)
  // Direct Download mode: Year, Language, Format, Size, Downloads
  const downloadCount = book.info?.Downloads?.[0];
  const starCount = book.info?.Stars?.[0];
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
        ...(downloadCount
          ? [
              {
                label: 'Downloads',
                value: `${Number(downloadCount).toLocaleString()}${starCount ? ` / ⭐${starCount}` : ''}`,
              },
            ]
          : []),
      ];

  // Extract rating and readers from display_fields for dedicated boxes (Universal mode)
  const ratingField = isMetadata && book.display_fields?.find((f) => f.icon === 'star');
  const readersField = isMetadata && book.display_fields?.find((f) => f.icon === 'users');
  // Other display fields (pages, editions, etc.) shown inline
  const otherDisplayFields =
    isMetadata && book.display_fields?.filter((f) => f.icon !== 'star' && f.icon !== 'users');

  // Use provider display name from backend, fall back to capitalized provider name
  const providerDisplay =
    book.provider_display_name ||
    (book.provider ? book.provider.charAt(0).toUpperCase() + book.provider.slice(1) : '');
  const isSquareCover = book.cover_aspect === 'square';
  const artworkMaxHeight = 'calc(90vh - 220px)';
  const artworkMaxWidth = isSquareCover
    ? 'min(45vw, 400px, calc(90vh - 220px))'
    : 'min(45vw, 520px, calc((90vh - 220px) / 1.6))';
  const additionalInfo =
    book.info && Object.keys(book.info).length > 0
      ? Object.entries(book.info).filter(([key]) => {
          const normalized = key.toLowerCase();
          return normalized !== 'language' && normalized !== 'year' && normalized !== 'downloads';
        })
      : [];
  const extendedInfoEntries = [[publisherInfo.label, publisherInfo.value], ...additionalInfo];
  const infoCardClass =
    'rounded-2xl border border-(--border-muted) px-4 py-3 text-sm bg-(--bg-soft) sm:bg-(--bg)';
  const infoLabelClass = 'text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400';
  const infoValueClass = 'text-gray-900 dark:text-gray-100';

  const modal = (
    <div className="modal-overlay active sm:px-6 sm:py-6">
      {isQueuing && buttonState.state !== 'download' && (
        <DetailsModalAutoClose clearQueuing={() => setIsQueuing(false)} closeModal={handleClose} />
      )}
      <button
        type="button"
        className="absolute inset-0 border-0 bg-transparent p-0"
        onClick={handleClose}
        tabIndex={-1}
        aria-label="Close details"
      />
      <div
        className={`details-container relative z-10 h-full w-full sm:h-auto ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex h-full flex-col overflow-hidden rounded-none border-0 border-(--border-muted) bg-(--bg) text-(--text) shadow-none sm:h-[90vh] sm:max-h-[90vh] sm:rounded-2xl sm:border sm:bg-(--bg-soft) sm:shadow-2xl">
          <header className="flex items-start gap-4 border-b border-(--border-muted) bg-(--bg) px-5 py-4 sm:bg-(--bg-soft)">
            <div className="flex-1 space-y-1">
              <p className="text-xs tracking-wide text-gray-500 uppercase dark:text-gray-400">
                Book
              </p>
              <h3 id={titleId} className="text-lg leading-snug font-semibold">
                {book.title || 'Untitled'}
              </h3>
              <p className="text-sm text-gray-600 dark:text-gray-300">
                {book.author || 'Unknown author'}
              </p>
            </div>
            <button
              type="button"
              onClick={handleClose}
              className="hover-action rounded-full p-2 text-gray-500 transition-colors hover:text-gray-900 dark:hover:text-gray-100"
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

          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6">
            <div className="flex flex-col gap-6 lg:min-h-0 lg:flex-row lg:items-stretch lg:gap-8">
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
                    className="flex w-full items-center justify-center rounded-xl border border-dashed border-(--border-muted) bg-(--bg)/60 p-6 text-sm text-gray-500 lg:h-full lg:max-w-none"
                    style={{ maxHeight: artworkMaxHeight, maxWidth: artworkMaxWidth }}
                  >
                    No cover
                  </div>
                )}
              </div>

              <div className="flex flex-1 flex-col gap-4 sm:gap-5 lg:min-h-0">
                {book.description && (
                  <div className={`${infoCardClass} space-y-1`}>
                    <p className={infoLabelClass}>Description</p>
                    <p className={`${infoValueClass} whitespace-pre-line`}>{book.description}</p>
                  </div>
                )}

                {/* Metadata grid - adapts columns based on mode and available data */}
                <div
                  className={`grid grid-cols-2 gap-3 lg:gap-4 ${isMetadata ? 'lg:grid-cols-2' : 'lg:grid-cols-4'}`}
                >
                  {metadata.map((item) => (
                    <div key={item.label} className={`${infoCardClass} space-y-1`}>
                      <p className={infoLabelClass}>{item.label}</p>
                      <p className={infoValueClass}>{item.value}</p>
                    </div>
                  ))}

                  {/* Rating box - Universal mode only */}
                  {ratingField && (
                    <div className={`${infoCardClass} space-y-1`}>
                      <p className={infoLabelClass}>{ratingField.label}</p>
                      <p className={`${infoValueClass} flex items-center gap-1.5`}>
                        <svg
                          className="h-4 w-4 text-amber-500"
                          fill="currentColor"
                          viewBox="0 0 20 20"
                        >
                          <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                        </svg>
                        {ratingField.value}
                      </p>
                    </div>
                  )}

                  {/* Readers box - Universal mode only */}
                  {readersField && (
                    <div className={`${infoCardClass} space-y-1`}>
                      <p className={infoLabelClass}>{readersField.label}</p>
                      <p className={`${infoValueClass} flex items-center gap-1.5`}>
                        <svg
                          className="h-4 w-4 text-gray-500"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          strokeWidth={1.5}
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z"
                          />
                        </svg>
                        {readersField.value}
                      </p>
                    </div>
                  )}

                  {/* Other display fields (length, narrator, format, etc.) - Universal mode only */}
                  {otherDisplayFields &&
                    otherDisplayFields.map((field) => (
                      <div key={field.label} className={`${infoCardClass} space-y-1`}>
                        <p className={infoLabelClass}>{field.label}</p>
                        <p className={infoValueClass}>{field.value}</p>
                      </div>
                    ))}
                </div>

                {isMetadata && isInLibrary(book.library) && (
                  <div className={`${infoCardClass} space-y-1`}>
                    <p className={infoLabelClass}>Library</p>
                    <LibraryBadge library={book.library} />
                  </div>
                )}

                {/* ISBN - Universal mode only */}
                {isMetadata && (book.isbn_13 || book.isbn_10) && (
                  <div className={`${infoCardClass} space-y-1`}>
                    <p className={infoLabelClass}>ISBN</p>
                    <p className={infoValueClass}>{book.isbn_13 || book.isbn_10}</p>
                  </div>
                )}

                {/* Series info - Universal mode only */}
                {isMetadata && book.series_name && (
                  <div className={`${infoCardClass} space-y-1`}>
                    <p className={infoLabelClass}>Series</p>
                    <div className="flex items-center justify-between gap-2">
                      <p className={infoValueClass}>
                        {book.series_position != null ? (
                          <>
                            #
                            {Number.isInteger(book.series_position)
                              ? book.series_position
                              : book.series_position}
                            {book.series_count ? ` of ${book.series_count}` : ''} in{' '}
                            {book.series_name}
                          </>
                        ) : (
                          book.series_name
                        )}
                      </p>
                      {onSearchSeries && (
                        <button
                          type="button"
                          onClick={() => {
                            const seriesName = book.series_name;
                            if (!seriesName) {
                              return;
                            }
                            onSearchSeries(seriesName, book.series_id);
                            handleClose();
                          }}
                          className="inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-600 transition-colors hover:bg-emerald-100 dark:bg-emerald-900/20 dark:text-emerald-400 dark:hover:bg-emerald-900/40"
                        >
                          <svg
                            className="h-3 w-3"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                            strokeWidth={2}
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
                            />
                          </svg>
                          View series
                        </button>
                      )}
                    </div>
                  </div>
                )}

                {/* Extended info (publisher, etc.) - Direct Download mode only */}
                {!isMetadata && extendedInfoEntries.length > 0 && (
                  <div className={`${infoCardClass} space-y-3`}>
                    <ul className="list-none space-y-3">
                      {extendedInfoEntries.map(([key, value]) => (
                        <li key={key} className="space-y-1">
                          <p className={infoLabelClass}>{key}</p>
                          <p className={infoValueClass}>
                            {Array.isArray(value) ? value.join(', ') : value}
                          </p>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          </div>

          <footer className="border-t border-(--border-muted) bg-(--bg) px-5 py-4 sm:bg-(--bg-soft)">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              {/* Source link - shown for both Universal and Direct Download modes */}
              {showBookSourceLink && (
                <a
                  href={book.source_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-xs font-medium text-gray-600 transition-colors hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-200"
                >
                  View on {isMetadata ? providerDisplay : 'Source'}
                  <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                    />
                  </svg>
                </a>
              )}
              <div className="flex w-full flex-col gap-3 sm:ml-auto sm:w-auto sm:flex-row sm:items-center">
                {hasBookTargets && bookProvider && bookProviderId && (
                  <BookTargetDropdown
                    provider={bookProvider}
                    bookId={bookProviderId}
                    onShowToast={onShowToast}
                    widthClassName="w-full sm:w-56"
                  />
                )}
                {/* Action button - mirrors search result action state/flow */}
                <button
                  type="button"
                  onClick={() => {
                    if (isMetadata) {
                      onFindDownloads?.(book);
                      return;
                    }
                    void handleDownload();
                  }}
                  disabled={
                    isMetadata ? buttonState.state === 'blocked' : buttonState.state !== 'download'
                  }
                  className={`rounded-lg px-5 py-2 text-sm font-medium text-white transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    downloadButtonClassName
                  }`}
                >
                  {isMetadata ? metadataActionText : buttonState.text}
                </button>
              </div>
            </div>
          </footer>
        </div>
      </div>
    </div>
  );

  if (typeof document === 'undefined') {
    return modal;
  }

  return createPortal(modal, document.body);
};

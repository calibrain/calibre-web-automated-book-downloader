import { useState } from 'react';

import { useSearchMode } from '../../contexts/SearchModeContext';
import type { Book, ButtonStateInfo } from '../../types';
import { getDownloadsCount, getStarCount } from '../../types';
import { bookSupportsTargets } from '../../utils/bookTargetLoader';
import { BookActionButton } from '../BookActionButton';
import { BookTargetDropdown } from '../BookTargetDropdown';
import { DisplayFieldBadges, DisplayFieldIcon, LibraryBadge } from '../shared';

const SkeletonLoader = () => (
  <div className="h-full w-full animate-pulse bg-linear-to-r from-gray-300 via-gray-200 to-gray-300 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700" />
);

interface CompactViewProps {
  book: Book;
  onDetails: (id: string) => Promise<void>;
  onDownload: (book: Book) => Promise<void>;
  onGetReleases: (book: Book) => Promise<void>;
  buttonState: ButtonStateInfo;
  showDetailsButton?: boolean;
  animationDelay?: number;
  showSeriesPosition?: boolean;
  index?: number;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

export const CompactView = ({
  book,
  onDetails,
  onDownload,
  onGetReleases,
  buttonState,
  showDetailsButton = false,
  animationDelay = 0,
  showSeriesPosition = false,
  onShowToast,
}: CompactViewProps) => {
  const { searchMode } = useSearchMode();
  const [isLoadingDetails, setIsLoadingDetails] = useState(false);
  const [isLoadingReleases, setIsLoadingReleases] = useState(false);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const targetProvider = book.provider;
  const targetBookId = book.provider_id;
  const microphoneField = book.display_fields?.find((field) => field.icon === 'microphone');
  let zIndex: number | undefined;
  if (dropdownOpen) {
    zIndex = 20;
  } else if (isHovered) {
    zIndex = 10;
  }

  const handleDetails = async (id: string) => {
    setIsLoadingDetails(true);
    try {
      await onDetails(id);
    } finally {
      setIsLoadingDetails(false);
    }
  };

  const handleGetReleases = async (selectedBook: Book) => {
    setIsLoadingReleases(true);
    try {
      await onGetReleases(selectedBook);
    } finally {
      setIsLoadingReleases(false);
    }
  };

  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- mouse handlers drive a decorative hover shadow only; no interactive behavior requiring keyboard support
    <article
      className="book-card animate-pop-up relative flex! h-[180px]! w-full flex-row! transition-shadow duration-300"
      style={{
        background: 'var(--bg-soft)',
        borderRadius: '.75rem',
        boxShadow: isHovered || dropdownOpen ? '0 10px 30px rgba(0, 0, 0, 0.15)' : 'none',
        zIndex,
        animationDelay: `${animationDelay}ms`,
        animationFillMode: 'both',
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div className="relative h-full w-[120px] shrink-0">
        <div className="absolute inset-0 overflow-hidden rounded-l-[.75rem]">
          {/* Series position badge */}
          {showSeriesPosition && book.series_position != null && (
            <div
              className="absolute top-2 left-2 z-10 rounded-md border border-emerald-700 bg-emerald-600 px-2 py-1 text-xs font-bold text-white"
              style={{
                boxShadow: '0 2px 8px rgba(0, 0, 0, 0.4), 0 1px 3px rgba(0, 0, 0, 0.3)',
                textShadow: '0 1px 2px rgba(0, 0, 0, 0.3)',
              }}
            >
              #{book.series_position}
            </div>
          )}
          <LibraryBadge library={book.library} overlay className="absolute top-2 right-2 z-10" />
          {book.preview && !imageError ? (
            <>
              {!imageLoaded && (
                <div className="absolute inset-0">
                  <SkeletonLoader />
                </div>
              )}
              <img
                src={book.preview}
                alt={book.title || 'Book cover'}
                className="h-full w-full"
                loading="lazy"
                style={{
                  opacity: imageLoaded ? 1 : 0,
                  transition: 'opacity 0.3s ease-in-out',
                  objectFit: 'cover',
                  objectPosition: 'top',
                }}
                onLoad={() => setImageLoaded(true)}
                onError={() => setImageError(true)}
              />
            </>
          ) : (
            <div
              className="flex h-full w-full items-center justify-center text-sm opacity-50"
              style={{ background: 'var(--border-muted)' }}
            >
              No Cover
            </div>
          )}

          <div
            className="pointer-events-none absolute inset-0 bg-white transition-opacity duration-300"
            style={{ opacity: isHovered || dropdownOpen ? 0.02 : 0 }}
          />
        </div>

        {!showDetailsButton && (
          <div
            className="absolute right-2 bottom-2 z-10 flex flex-col gap-1.5 transition-all duration-300"
            style={{
              opacity: isHovered || dropdownOpen || isLoadingDetails ? 1 : 0,
              pointerEvents: isHovered || dropdownOpen || isLoadingDetails ? 'auto' : 'none',
            }}
          >
            {bookSupportsTargets(book) && targetProvider && targetBookId && (
              <BookTargetDropdown
                provider={targetProvider}
                bookId={targetBookId}
                onShowToast={onShowToast}
                variant="icon"
                className="h-8 w-8 bg-white/95 shadow-lg hover:scale-110 dark:bg-neutral-800/95"
                onOpenChange={setDropdownOpen}
              />
            )}
            <button
              type="button"
              className="flex h-8 w-8 items-center justify-center rounded-full bg-white/95 shadow-lg transition-all duration-300 hover:scale-110 dark:bg-neutral-800/95"
              onClick={(e) => {
                e.stopPropagation();
                void handleDetails(book.id);
              }}
              disabled={isLoadingDetails}
              aria-label="Book details"
            >
              {isLoadingDetails ? (
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
              ) : (
                <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
                  />
                </svg>
              )}
            </button>
          </div>
        )}
      </div>

      <div className="flex min-w-0 flex-1 flex-col p-3 py-2">
        <div className="min-w-0 space-y-0.5">
          <h3
            className="line-clamp-3 min-w-0 text-base leading-tight font-semibold"
            title={book.title || 'Untitled'}
          >
            {book.title || 'Untitled'}
          </h3>
          <p className="min-w-0 truncate text-xs opacity-80">{book.author || 'Unknown author'}</p>
          <div className="text-xs opacity-70">
            <span>{book.year || '-'}</span>
          </div>
        </div>

        <div className="mt-auto flex flex-col gap-2">
          {searchMode === 'universal' && book.display_fields && book.display_fields.length > 0 ? (
            <>
              <DisplayFieldBadges
                fields={book.display_fields.filter(
                  (f) => f.icon !== 'editions' && f.icon !== 'microphone',
                )}
                className="text-xs opacity-70"
              />
              {microphoneField && (
                <div className="flex items-center gap-0.5 text-xs opacity-70">
                  <DisplayFieldIcon icon="microphone" />
                  <span>{microphoneField.value}</span>
                </div>
              )}
            </>
          ) : (
            <div className="flex flex-wrap gap-1 text-xs opacity-70">
              <span>{book.language || '-'}</span>
              <span>•</span>
              <span>{book.format || '-'}</span>
              {book.size && (
                <>
                  <span>•</span>
                  <span>{book.size}</span>
                </>
              )}
              {(() => {
                const d = getDownloadsCount(book);
                const s = getStarCount(book);
                if (d == null || d <= 0) return null;
                const text =
                  s != null && s > 0 ? `${d.toLocaleString()}/⭐${s}` : d.toLocaleString();
                return (
                  <>
                    {' '}
                    <span>•</span> <span>{text}</span>{' '}
                  </>
                );
              })()}
            </div>
          )}

          {showDetailsButton ? (
            <div className="flex gap-1.5">
              <button
                type="button"
                className="flex shrink-0 items-center justify-center gap-1 rounded-sm border px-2 py-1.5 text-xs"
                onClick={() => {
                  void handleDetails(book.id);
                }}
                style={{ borderColor: 'var(--border-muted)' }}
                disabled={isLoadingDetails}
              >
                <span className="details-button-text">
                  {isLoadingDetails ? 'Loading' : 'Details'}
                </span>
                {isLoadingDetails && (
                  <div className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                )}
              </button>
              <BookActionButton
                book={book}
                buttonState={buttonState}
                onDownload={onDownload}
                onGetReleases={(selectedBook) => {
                  void handleGetReleases(selectedBook);
                }}
                isLoadingReleases={isLoadingReleases}
                size="sm"
                className="flex-1"
              />
            </div>
          ) : (
            <BookActionButton
              book={book}
              buttonState={buttonState}
              onDownload={onDownload}
              onGetReleases={(selectedBook) => {
                void handleGetReleases(selectedBook);
              }}
              isLoadingReleases={isLoadingReleases}
              size="sm"
              fullWidth
            />
          )}
        </div>
      </div>
    </article>
  );
};

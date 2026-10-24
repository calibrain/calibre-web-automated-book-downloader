import { useState } from 'react';

import { useSearchMode } from '../../contexts/SearchModeContext';
import { t } from '../../i18n';
import type { Book, ButtonStateInfo } from '../../types';
import { getDownloadsCount } from '../../types';
import { bookSupportsTargets } from '../../utils/bookTargetLoader';
import { BookActionButton } from '../BookActionButton';
import { BookTargetDropdown } from '../BookTargetDropdown';
import { DisplayFieldBadges, LibraryBadge } from '../shared';

const SkeletonLoader = () => (
  <div className="h-full w-full animate-pulse bg-linear-to-r from-gray-300 via-gray-200 to-gray-300 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700" />
);

interface CardViewProps {
  book: Book;
  onDetails: (id: string) => Promise<void>;
  onDownload: (book: Book) => Promise<void>;
  onGetReleases: (book: Book) => Promise<void>;
  buttonState: ButtonStateInfo;
  animationDelay?: number;
  showSeriesPosition?: boolean;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

export const CardView = ({
  book,
  onDetails,
  onDownload,
  onGetReleases,
  buttonState,
  animationDelay = 0,
  showSeriesPosition = false,
  onShowToast,
}: CardViewProps) => {
  const { searchMode } = useSearchMode();
  const [isLoadingDetails, setIsLoadingDetails] = useState(false);
  const [isLoadingReleases, setIsLoadingReleases] = useState(false);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const targetProvider = book.provider;
  const targetBookId = book.provider_id;
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
      className="book-card space-between animate-pop-up relative flex h-full w-full flex-col transition-shadow duration-300 max-sm:h-[180px] max-sm:flex-row sm:max-w-[292px] sm:flex-col"
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
      <div
        className="relative w-full max-sm:h-full max-sm:w-[120px] max-sm:shrink-0 sm:w-full"
        style={{ aspectRatio: book.cover_aspect === 'square' ? '1/1' : '2/3' }}
      >
        <div className="absolute inset-0 overflow-hidden max-sm:rounded-l-[.75rem] sm:rounded-t-[.75rem]">
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
                alt={book.title || t('Book cover')}
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
              {t('No Cover')}
            </div>
          )}

          <div
            className="pointer-events-none absolute inset-0 bg-white transition-opacity duration-300"
            style={{ opacity: isHovered || dropdownOpen ? 0.02 : 0 }}
          />
        </div>

        <div
          className="absolute right-2 bottom-2 z-10 flex flex-col gap-1.5 transition-all duration-300 max-sm:hidden"
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
            aria-label={t('Book details')}
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
      </div>

      <div className="flex flex-col gap-3 p-4 max-sm:min-w-0 max-sm:flex-1 max-sm:justify-between max-sm:gap-2 max-sm:p-3 max-sm:py-2 sm:flex sm:flex-1 sm:flex-col sm:justify-end">
        <div className="space-y-1 max-sm:min-w-0 max-sm:space-y-0.5">
          <h3
            className="line-clamp-2 text-base leading-tight font-semibold max-sm:line-clamp-3 max-sm:min-w-0"
            title={book.title || t('Untitled')}
          >
            {book.title || t('Untitled')}
          </h3>
          <p className="truncate text-sm opacity-80 max-sm:min-w-0 max-sm:text-xs">
            {book.author || t('Unknown author')}
          </p>
          {searchMode === 'universal' && book.display_fields && book.display_fields.length > 0 ? (
            <div className="flex flex-wrap gap-2 text-xs opacity-70 max-sm:gap-1 max-sm:text-[10px]">
              <span>{book.year || '-'}</span>
              <span>•</span>
              <DisplayFieldBadges
                fields={book.display_fields.filter((f) => f.icon !== 'editions')}
              />
            </div>
          ) : (
            <div className="flex flex-wrap gap-2 text-xs opacity-70 max-sm:gap-1 max-sm:text-[10px]">
              <span>{book.year || '-'}</span>
              <span>•</span>
              <span>{book.language || '-'}</span>
              <span>•</span>
              <span>{book.format || '-'}</span>
              {book.size && (
                <>
                  <span>•</span>
                  <span>{book.size}</span>
                </>
              )}
              {searchMode !== 'universal' &&
                (() => {
                  const d = getDownloadsCount(book);
                  return d != null && d > 0 ? (
                    <>
                      {' '}
                      <span>•</span> <span>{d.toLocaleString()}</span>{' '}
                    </>
                  ) : null;
                })()}
            </div>
          )}
        </div>

        <div className="flex gap-1.5 sm:hidden">
          <button
            type="button"
            className="flex flex-1 items-center justify-center gap-1 rounded-sm border px-2 py-1.5 text-xs"
            onClick={() => {
              void handleDetails(book.id);
            }}
            style={{ borderColor: 'var(--border-muted)' }}
            disabled={isLoadingDetails}
          >
            <span className="details-button-text">
              {isLoadingDetails ? t('Loading') : t('Details')}
            </span>
            <div
              className={`details-spinner h-3 w-3 rounded-full border-2 border-current border-t-transparent ${isLoadingDetails ? '' : 'hidden'}`}
            />
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
      </div>

      <BookActionButton
        book={book}
        buttonState={buttonState}
        onDownload={onDownload}
        onGetReleases={(selectedBook) => {
          void handleGetReleases(selectedBook);
        }}
        isLoadingReleases={isLoadingReleases}
        className="hidden rounded-none sm:flex"
        fullWidth
        style={{
          borderBottomLeftRadius: '.75rem',
          borderBottomRightRadius: '.75rem',
        }}
      />
    </article>
  );
};

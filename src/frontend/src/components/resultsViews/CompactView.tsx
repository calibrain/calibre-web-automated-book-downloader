import { useState } from 'react';
import { Book, ButtonStateInfo } from '../../types';
import { useSearchMode } from '../../contexts/SearchModeContext';
import { BookActionButton } from '../BookActionButton';
import { DisplayFieldBadges } from '../shared';

const SkeletonLoader = () => (
  <div className="w-full h-full bg-gradient-to-r from-gray-300 via-gray-200 to-gray-300 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 animate-pulse" />
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
}

export const CompactView = ({ book, onDetails, onDownload, onGetReleases, buttonState, showDetailsButton = false, animationDelay = 0, showSeriesPosition = false }: CompactViewProps) => {
  const { searchMode } = useSearchMode();
  const [isLoadingDetails, setIsLoadingDetails] = useState(false);
  const [isLoadingReleases, setIsLoadingReleases] = useState(false);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);
  const [isHovered, setIsHovered] = useState(false);

  const handleDetails = async (id: string) => {
    setIsLoadingDetails(true);
    try {
      await onDetails(id);
    } finally {
      setIsLoadingDetails(false);
    }
  };

  const handleGetReleases = async (book: Book) => {
    setIsLoadingReleases(true);
    try {
      await onGetReleases(book);
    } finally {
      setIsLoadingReleases(false);
    }
  };

  return (
    <article
      className="book-card overflow-hidden !flex !flex-row w-full !h-[180px] transition-shadow duration-300 animate-slide-up will-change-transform"
      style={{
        background: 'var(--bg-soft)',
        borderRadius: '.75rem',
        boxShadow: isHovered ? '0 10px 30px rgba(0, 0, 0, 0.15)' : 'none',
        animationDelay: `${animationDelay}ms`,
        animationFillMode: 'both',
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div className="relative w-[120px] h-full flex-shrink-0">
        {/* Series position badge */}
        {showSeriesPosition && book.series_position != null && (
          <div
            className="absolute top-2 left-2 z-10 px-2 py-1 text-xs font-bold text-white bg-emerald-600 rounded-md border border-emerald-700"
            style={{
              boxShadow: '0 2px 8px rgba(0, 0, 0, 0.4), 0 1px 3px rgba(0, 0, 0, 0.3)',
              textShadow: '0 1px 2px rgba(0, 0, 0, 0.3)',
            }}
          >
            #{book.series_position}
          </div>
        )}
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
              className="w-full h-full"
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
          <div className="w-full h-full flex items-center justify-center text-sm opacity-50" style={{ background: 'var(--border-muted)' }}>
            No Cover
          </div>
        )}

        <div className="absolute inset-0 bg-white transition-opacity duration-300 pointer-events-none" style={{ opacity: isHovered ? 0.02 : 0 }} />

        {!showDetailsButton && (
          <button
            className="absolute bottom-2 right-2 w-8 h-8 rounded-full bg-white/90 dark:bg-gray-800/90 backdrop-blur-sm flex items-center justify-center transition-all duration-300 shadow-lg hover:scale-110"
            style={{
              opacity: isHovered || isLoadingDetails ? 1 : 0,
              pointerEvents: isHovered || isLoadingDetails ? 'auto' : 'none',
            }}
            onClick={(e) => {
              e.stopPropagation();
              handleDetails(book.id);
            }}
            disabled={isLoadingDetails}
            aria-label="Book details"
          >
            {isLoadingDetails ? (
              <div className="w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin" />
            ) : (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            )}
          </button>
        )}
      </div>

      <div className="p-3 py-2 flex flex-col flex-1 min-w-0">
        <div className="space-y-0.5 min-w-0">
          <h3 className="font-semibold leading-tight line-clamp-3 text-base min-w-0" title={book.title || 'Untitled'}>
            {book.title || 'Untitled'}
          </h3>
          <p className="text-xs opacity-80 truncate min-w-0">{book.author || 'Unknown author'}</p>
          <div className="text-xs opacity-70">
            <span>{book.year || '-'}</span>
          </div>
        </div>

        <div className="mt-auto flex flex-col gap-2">
          {searchMode === 'universal' && book.display_fields && book.display_fields.length > 0 ? (
            <DisplayFieldBadges fields={book.display_fields} className="text-xs opacity-70" />
          ) : (
            <div className="text-xs opacity-70 flex flex-wrap gap-1">
              <span>{book.language || '-'}</span>
              <span>•</span>
              <span>{book.format || '-'}</span>
              {book.size && (
                <>
                  <span>•</span>
                  <span>{book.size}</span>
                </>
              )}
            </div>
          )}

          {showDetailsButton ? (
            <div className="flex gap-1.5">
              <button
                className="px-2 py-1.5 rounded border text-xs flex-shrink-0 flex items-center justify-center gap-1"
                onClick={() => handleDetails(book.id)}
                style={{ borderColor: 'var(--border-muted)' }}
                disabled={isLoadingDetails}
              >
                <span className="details-button-text">{isLoadingDetails ? 'Loading' : 'Details'}</span>
                {isLoadingDetails && <div className="w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin" />}
              </button>
              <BookActionButton
                book={book}
                buttonState={buttonState}
                onDownload={onDownload}
                onGetReleases={handleGetReleases}
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
              onGetReleases={handleGetReleases}
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


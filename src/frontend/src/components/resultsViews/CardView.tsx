import { useState } from 'react';
import { Book, ButtonStateInfo } from '../../types';
import { useSearchMode } from '../../contexts/SearchModeContext';
import { BookActionButton } from '../BookActionButton';
import { DisplayFieldBadges } from '../shared';

const SkeletonLoader = () => (
  <div className="w-full h-full bg-gradient-to-r from-gray-300 via-gray-200 to-gray-300 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 animate-pulse" />
);

interface CardViewProps {
  book: Book;
  onDetails: (id: string) => Promise<void>;
  onDownload: (book: Book) => Promise<void>;
  onGetReleases: (book: Book) => Promise<void>;
  buttonState: ButtonStateInfo;
  animationDelay?: number;
  showSeriesPosition?: boolean;
}

export const CardView = ({ book, onDetails, onDownload, onGetReleases, buttonState, animationDelay = 0, showSeriesPosition = false }: CardViewProps) => {
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
      className="book-card overflow-hidden flex flex-col sm:flex-col max-sm:flex-row space-between w-full sm:max-w-[292px] max-sm:h-[180px] h-full transition-shadow duration-300 animate-slide-up will-change-transform"
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
      <div className="relative w-full sm:w-full max-sm:w-[120px] max-sm:h-full max-sm:flex-shrink-0 group" style={{ aspectRatio: '2/3' }}>
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

        <div
          className="absolute inset-0 bg-white transition-opacity duration-300 pointer-events-none"
          style={{ opacity: isHovered ? 0.02 : 0 }}
        />

        <button
          className="absolute bottom-2 right-2 w-8 h-8 rounded-full bg-white/90 dark:bg-gray-800/90 backdrop-blur-sm flex items-center justify-center transition-all duration-300 shadow-lg hover:scale-110 max-sm:hidden"
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
      </div>

      <div className="p-4 max-sm:p-3 max-sm:py-2 flex flex-col gap-3 max-sm:gap-2 max-sm:flex-1 max-sm:justify-between max-sm:min-w-0 sm:flex-1 sm:flex sm:flex-col sm:justify-end">
        <div className="space-y-1 max-sm:space-y-0.5 max-sm:min-w-0">
          <h3 className="font-semibold leading-tight line-clamp-2 text-base max-sm:line-clamp-3 max-sm:min-w-0" title={book.title || 'Untitled'}>
            {book.title || 'Untitled'}
          </h3>
          <p className="text-sm max-sm:text-xs opacity-80 truncate max-sm:min-w-0">{book.author || 'Unknown author'}</p>
          {searchMode === 'universal' && book.display_fields && book.display_fields.length > 0 ? (
            <div className="text-xs max-sm:text-[10px] opacity-70 flex flex-wrap gap-2 max-sm:gap-1">
              <span>{book.year || '-'}</span>
              <span>•</span>
              <DisplayFieldBadges fields={book.display_fields} />
            </div>
          ) : (
            <div className="text-xs max-sm:text-[10px] opacity-70 flex flex-wrap gap-2 max-sm:gap-1">
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
            </div>
          )}
        </div>

        <div className="flex gap-1.5 sm:hidden">
          <button
            className="px-2 py-1.5 rounded border text-xs flex-1 flex items-center justify-center gap-1"
            onClick={() => handleDetails(book.id)}
            style={{ borderColor: 'var(--border-muted)' }}
            disabled={isLoadingDetails}
          >
            <span className="details-button-text">{isLoadingDetails ? 'Loading' : 'Details'}</span>
            <div
              className={`details-spinner w-3 h-3 border-2 border-current border-t-transparent rounded-full ${isLoadingDetails ? '' : 'hidden'}`}
            />
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
      </div>

      <BookActionButton
        book={book}
        buttonState={buttonState}
        onDownload={onDownload}
        onGetReleases={handleGetReleases}
        isLoadingReleases={isLoadingReleases}
        className="hidden sm:flex rounded-none"
        fullWidth
        style={{
          borderBottomLeftRadius: '.75rem',
          borderBottomRightRadius: '.75rem',
        }}
      />
    </article>
  );
};


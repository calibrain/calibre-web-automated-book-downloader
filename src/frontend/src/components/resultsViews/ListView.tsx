import { useState } from 'react';
import { Book, ButtonStateInfo } from '../../types';
import { useSearchMode } from '../../contexts/SearchModeContext';
import { BookActionButton } from '../BookActionButton';
import { DisplayFieldIcon, DisplayFieldBadge } from '../shared';
import { getFormatColor, getLanguageColor } from '../../utils/colorMaps';

interface ListViewProps {
  books: Book[];
  onDetails: (id: string) => Promise<void>;
  onDownload: (book: Book) => Promise<void>;
  onGetReleases: (book: Book) => Promise<void>;
  getButtonState: (bookId: string) => ButtonStateInfo;
  getUniversalButtonState: (bookId: string) => ButtonStateInfo;
  showSeriesPosition?: boolean;
}

const ListViewThumbnail = ({ preview, title }: { preview?: string; title?: string }) => {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);

  if (!preview || imageError) {
    return (
      <div
        className="w-7 h-10 sm:w-10 sm:h-14 rounded bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-[8px] sm:text-[9px] font-medium text-gray-500 dark:text-gray-300"
        aria-label="No cover available"
      >
        No Cover
      </div>
    );
  }

  return (
    <div className="relative w-7 h-10 sm:w-10 sm:h-14 rounded overflow-hidden bg-gray-100 dark:bg-gray-800 border border-white/40 dark:border-gray-700/70">
      {!imageLoaded && (
        <div className="absolute inset-0 bg-gradient-to-r from-gray-200 via-gray-100 to-gray-200 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 animate-pulse" />
      )}
      <img
        src={preview}
        alt={title || 'Book cover'}
        className="w-full h-full object-cover object-top"
        loading="lazy"
        onLoad={() => setImageLoaded(true)}
        onError={() => setImageError(true)}
        style={{ opacity: imageLoaded ? 1 : 0, transition: 'opacity 0.2s ease-in-out' }}
      />
    </div>
  );
};

export const ListView = ({ books, onDetails, onDownload, onGetReleases, getButtonState, getUniversalButtonState, showSeriesPosition = false }: ListViewProps) => {
  const { searchMode } = useSearchMode();
  const [detailsLoadingId, setDetailsLoadingId] = useState<string | null>(null);
  const [releasesLoadingId, setReleasesLoadingId] = useState<string | null>(null);

  if (books.length === 0) {
    return null;
  }

  const handleDetails = async (bookId: string) => {
    setDetailsLoadingId(bookId);
    try {
      await onDetails(bookId);
    } finally {
      setDetailsLoadingId((current) => (current === bookId ? null : current));
    }
  };

  const handleGetReleases = async (book: Book) => {
    setReleasesLoadingId(book.id);
    try {
      await onGetReleases(book);
    } finally {
      setReleasesLoadingId((current) => (current === book.id ? null : current));
    }
  };

  return (
    <article
      className="w-full overflow-hidden rounded-lg sm:rounded-2xl"
      style={{
        background: 'var(--bg-soft)',
        boxShadow: '0 10px 30px rgba(15, 23, 42, 0.08)',
      }}
      role="region"
      aria-label="List view of books"
    >
      <div className="divide-y divide-gray-200/60 dark:divide-gray-800/60 w-full">
        {books.map((book, index) => {
          // Use appropriate button state function based on search mode
          const buttonState = searchMode === 'universal'
            ? getUniversalButtonState(book.id)
            : getButtonState(book.id);
          const isLoadingDetails = detailsLoadingId === book.id;

          // Compute color styles for direct mode badges
          const languageColor = getLanguageColor(book.language);
          const formatColor = getFormatColor(book.format);

          return (
            <div
              key={book.id}
              className="px-1.5 sm:px-2 py-1.5 sm:py-2 transition-colors duration-200 hover-row w-full animate-slide-up will-change-transform"
              style={{
                animationDelay: `${index * 50}ms`,
                animationFillMode: 'both',
              }}
              role="article"
            >
              {/* Mobile and Desktop: Single row layout */}
              {/* Universal mode uses separate columns for each display field, direct mode uses language/format/size */}
              <div className={`grid items-center gap-2 sm:gap-y-1 sm:gap-x-0.5 w-full ${
                searchMode === 'universal'
                  ? 'grid-cols-[auto_minmax(0,1fr)_auto_auto] sm:grid-cols-[auto_minmax(0,2fr)_minmax(50px,0.25fr)_minmax(80px,0.4fr)_minmax(80px,0.4fr)_auto]'
                  : 'grid-cols-[auto_minmax(0,1fr)_auto_auto] sm:grid-cols-[auto_minmax(0,2fr)_minmax(50px,0.25fr)_minmax(60px,0.3fr)_minmax(60px,0.3fr)_minmax(60px,0.3fr)_auto]'
              }`}>
                {/* Thumbnail */}
                <div className="flex items-center pl-1 sm:pl-3">
                  <ListViewThumbnail preview={book.preview} title={book.title} />
                </div>

                {/* Title and Author */}
                <div className="min-w-0 flex flex-col justify-center sm:pl-3">
                  <h3 className="font-semibold text-xs min-[400px]:text-sm sm:text-base leading-tight line-clamp-1 sm:line-clamp-2 flex items-center gap-2" title={book.title || 'Untitled'}>
                    {showSeriesPosition && book.series_position != null && (
                      <span className="inline-flex mr-1.5 px-1.5 py-0.5 text-[10px] sm:text-xs font-bold text-white bg-emerald-600 rounded flex-shrink-0">
                        #{book.series_position}
                      </span>
                    )}
                    <span className="truncate">{book.title || 'Untitled'}</span>
                  </h3>
                  <p className="text-[10px] min-[400px]:text-xs sm:text-sm text-gray-600 dark:text-gray-300 truncate">
                    {book.author || 'Unknown author'}
                    {book.year && <span className="sm:hidden"> • {book.year}</span>}
                  </p>
                </div>

                {/* Mobile universal mode info */}
                <div className="flex sm:hidden flex-col items-end text-[10px] opacity-70 leading-tight">
                  {searchMode === 'universal' && book.display_fields && book.display_fields.length > 0 ? (
                    book.display_fields.slice(0, 2).map((field, idx) => (
                      <span key={idx} className="flex items-center gap-0.5" title={field.label}>
                        <DisplayFieldIcon icon={field.icon} />
                        <span>{field.value}</span>
                      </span>
                    ))
                  ) : (
                    <>
                      <span>{book.format || '-'}</span>
                      {book.size && <span>{book.size}</span>}
                    </>
                  )}
                </div>

                {/* Year - Desktop only */}
                <div className="hidden sm:flex text-xs text-gray-700 dark:text-gray-200 justify-center">
                  {book.year || '-'}
                </div>

                {/* Universal mode: Display fields as separate columns - Desktop only */}
                {searchMode === 'universal' && (
                  <>
                    {/* First display field column */}
                    <div className="hidden sm:flex justify-center">
                      {book.display_fields && book.display_fields[0] ? (
                        <DisplayFieldBadge field={book.display_fields[0]} />
                      ) : (
                        <span className="text-xs text-gray-500">-</span>
                      )}
                    </div>
                    {/* Second display field column */}
                    <div className="hidden sm:flex justify-center">
                      {book.display_fields && book.display_fields[1] ? (
                        <DisplayFieldBadge field={book.display_fields[1]} />
                      ) : (
                        <span className="text-xs text-gray-500">-</span>
                      )}
                    </div>
                  </>
                )}

                {/* Direct mode: Language Badge - Desktop only */}
                {searchMode !== 'universal' && (
                  <div className="hidden sm:flex justify-center">
                    <span
                      className={`${languageColor.bg} ${languageColor.text} text-[11px] font-semibold px-2 py-0.5 rounded-lg uppercase tracking-wide`}
                      title={book.language || 'Unknown'}
                    >
                      {book.language || '-'}
                    </span>
                  </div>
                )}

                {/* Direct mode: Format Badge - Desktop only */}
                {searchMode !== 'universal' && (
                  <div className="hidden sm:flex justify-center">
                    <span
                      className={`${formatColor.bg} ${formatColor.text} text-[11px] font-semibold px-2 py-0.5 rounded-lg uppercase tracking-wide`}
                      title={book.format || 'Unknown'}
                    >
                      {book.format || '-'}
                    </span>
                  </div>
                )}

                {/* Direct mode: Size - Desktop only */}
                {searchMode !== 'universal' && (
                  <div className="hidden sm:flex text-xs text-gray-700 dark:text-gray-200 justify-center">
                    {book.size || '-'}
                  </div>
                )}

                {/* Action Buttons */}
                <div className="flex flex-row justify-end gap-0.5 sm:gap-1 sm:pr-3">
                  <button
                    className="flex items-center justify-center p-1.5 sm:p-2 rounded-full text-gray-600 dark:text-gray-200 hover-action transition-all duration-200"
                    onClick={() => handleDetails(book.id)}
                    disabled={isLoadingDetails}
                    aria-label={`View details for ${book.title || 'this book'}`}
                  >
                    {isLoadingDetails ? (
                      <div className="w-4 h-4 sm:w-5 sm:h-5 border-2 border-current border-t-transparent rounded-full animate-spin" />
                    ) : (
                      <svg className="w-4 h-4 sm:w-5 sm:h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.5">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M12 20a8 8 0 100-16 8 8 0 000 16z" />
                      </svg>
                    )}
                  </button>
                  <BookActionButton
                    book={book}
                    buttonState={buttonState}
                    onDownload={onDownload}
                    onGetReleases={handleGetReleases}
                    isLoadingReleases={releasesLoadingId === book.id}
                    variant="icon"
                    size="md"
                  />
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </article>
  );
};


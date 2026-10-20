import { useState } from 'react';

import { useSearchMode } from '../../contexts/SearchModeContext';
import type { Book, ButtonStateInfo, DisplayField } from '../../types';
import { getDownloadsCount, getStarCount } from '../../types';
import { bookSupportsTargets } from '../../utils/bookTargetLoader';
import { getFormatColor, getLanguageColor } from '../../utils/colorMaps';
import { BookActionButton } from '../BookActionButton';
import { BookTargetDropdown } from '../BookTargetDropdown';
import { DisplayFieldIcon, DisplayFieldBadge, LibraryBadge } from '../shared';

interface ListViewProps {
  books: Book[];
  onDetails: (id: string) => Promise<void>;
  onDownload: (book: Book) => Promise<void>;
  onGetReleases: (book: Book) => Promise<void>;
  getButtonState: (bookId: string) => ButtonStateInfo;
  getUniversalButtonState: (bookId: string) => ButtonStateInfo;
  showSeriesPosition?: boolean;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

const getKeyedDisplayFields = (fields: DisplayField[]) => {
  const signatureCounts = new Map<string, number>();

  return fields.map((field) => {
    const signature = [field.icon ?? 'none', field.label, field.value].join('|');
    const nextCount = (signatureCounts.get(signature) ?? 0) + 1;
    signatureCounts.set(signature, nextCount);

    return {
      field,
      key: nextCount === 1 ? signature : `${signature}|${nextCount}`,
    };
  });
};

const ListViewThumbnail = ({
  preview,
  title,
  coverAspect,
}: {
  preview?: string;
  title?: string;
  coverAspect?: string;
}) => {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);
  const isSquare = coverAspect === 'square';
  const sizeClass = isSquare ? 'w-10 h-10 sm:w-14 sm:h-14' : 'w-7 h-10 sm:w-10 sm:h-14';

  if (!preview || imageError) {
    return (
      <div
        className={`${sizeClass} flex items-center justify-center rounded-sm bg-gray-200 text-[8px] font-medium text-gray-500 sm:text-[9px] dark:bg-gray-700 dark:text-gray-300`}
        aria-label="No cover available"
      >
        No Cover
      </div>
    );
  }

  return (
    <div
      className={`relative ${sizeClass} overflow-hidden rounded-sm border border-white/40 bg-gray-100 dark:border-gray-700/70 dark:bg-gray-800`}
    >
      {!imageLoaded && (
        <div className="absolute inset-0 animate-pulse bg-linear-to-r from-gray-200 via-gray-100 to-gray-200 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700" />
      )}
      <img
        src={preview}
        alt={title || 'Book cover'}
        className={`h-full w-full object-cover ${isSquare ? 'object-center' : 'object-top'}`}
        loading="lazy"
        onLoad={() => setImageLoaded(true)}
        onError={() => setImageError(true)}
        style={{ opacity: imageLoaded ? 1 : 0, transition: 'opacity 0.2s ease-in-out' }}
      />
    </div>
  );
};

export const ListView = ({
  books,
  onDetails,
  onDownload,
  onGetReleases,
  getButtonState,
  getUniversalButtonState,
  showSeriesPosition = false,
  onShowToast,
}: ListViewProps) => {
  const { searchMode } = useSearchMode();
  const [detailsLoadingId, setDetailsLoadingId] = useState<string | null>(null);
  const [releasesLoadingId, setReleasesLoadingId] = useState<string | null>(null);
  const [openDropdownBookId, setOpenDropdownBookId] = useState<string | null>(null);

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
      className="w-full rounded-lg sm:rounded-2xl"
      style={{
        background: 'var(--bg-soft)',
        boxShadow: '0 10px 30px rgba(15, 23, 42, 0.08)',
      }}
      role="region"
      aria-label="List view of books"
    >
      <div className="w-full divide-y divide-gray-200/60 dark:divide-gray-800/60">
        {books.map((book, index) => {
          // Use appropriate button state function based on search mode
          const buttonState =
            searchMode === 'universal' ? getUniversalButtonState(book.id) : getButtonState(book.id);
          const isLoadingDetails = detailsLoadingId === book.id;
          const ratingField = book.display_fields?.find((field) => field.icon === 'star');
          const lengthField = book.display_fields?.find(
            (field) => field.icon === 'clock' || field.icon === 'book',
          );
          const narratorField = book.display_fields?.find((field) => field.icon === 'microphone');
          const targetProvider = book.provider;
          const targetBookId = book.provider_id;
          const mobileDisplayFields =
            searchMode === 'universal' && book.display_fields
              ? getKeyedDisplayFields(
                  book.display_fields.filter((field) => field.icon !== 'editions').slice(0, 2),
                )
              : [];

          // Compute color styles for direct mode badges
          const languageColor = getLanguageColor(book.language);
          const formatColor = getFormatColor(book.format);

          return (
            <div
              key={book.id}
              className="hover-row animate-pop-up relative w-full px-1.5 py-1.5 transition-colors duration-200 sm:px-2 sm:py-2"
              style={{
                zIndex: openDropdownBookId === book.id ? 30 : undefined,
                animationDelay: `${index * 50}ms`,
                animationFillMode: 'both',
              }}
              role="article"
            >
              {/* Mobile and Desktop: Single row layout */}
              {/* Universal mode uses separate columns for each display field, direct mode uses language/format/size */}
              <div
                className={`grid w-full items-center gap-2 sm:gap-x-0.5 sm:gap-y-1 ${
                  searchMode === 'universal'
                    ? 'grid-cols-[auto_28px_auto_auto] sm:grid-cols-[auto_28px_minmax(0,2fr)_minmax(50px,0.25fr)_minmax(90px,0.5fr)_minmax(90px,0.5fr)_minmax(120px,0.7fr)_auto]'
                    : 'grid-cols-[auto_28px_auto_auto] sm:grid-cols-[auto_28px_minmax(0,2fr)_minmax(50px,0.25fr)_minmax(60px,0.3fr)_minmax(60px,0.3fr)_minmax(60px,0.3fr)_minmax(70px,0.35fr)_auto]'
                }`}
              >
                {/* Thumbnail */}
                <div className="flex items-center pl-1 sm:pl-3">
                  <ListViewThumbnail
                    preview={book.preview}
                    title={book.title}
                    coverAspect={book.cover_aspect}
                  />
                </div>

                {/* Release number */}
                <span className="w-7 shrink-0 text-center text-xs font-medium text-zinc-400 dark:text-zinc-500">
                  {index + 1}
                </span>

                {/* Title and Author */}
                <div className="flex min-w-0 flex-col justify-center sm:pl-3">
                  <h3
                    className="line-clamp-1 flex items-center gap-2 text-xs leading-tight font-semibold min-[400px]:text-sm sm:line-clamp-2 sm:text-base"
                    title={book.title || 'Untitled'}
                  >
                    {showSeriesPosition && book.series_position != null && (
                      <span
                        className="mr-1.5 inline-flex shrink-0 rounded-sm border border-emerald-700 bg-emerald-600 px-1.5 py-0.5 text-[10px] font-bold text-white sm:text-xs"
                        style={{
                          boxShadow: '0 1px 4px rgba(0, 0, 0, 0.3)',
                          textShadow: '0 1px 2px rgba(0, 0, 0, 0.3)',
                        }}
                      >
                        #{book.series_position}
                      </span>
                    )}
                    <span className="truncate">{book.title || 'Untitled'}</span>
                  </h3>
                  <p className="truncate text-[10px] text-gray-600 min-[400px]:text-xs sm:text-sm dark:text-gray-300">
                    {book.author || 'Unknown author'}
                    {book.year && <span className="sm:hidden"> • {book.year}</span>}
                  </p>
                  <LibraryBadge library={book.library} className="mt-0.5" />
                </div>

                {/* Mobile universal mode info */}
                <div className="flex flex-col items-end text-[10px] leading-tight opacity-70 sm:hidden">
                  {mobileDisplayFields.length > 0 ? (
                    mobileDisplayFields.map(({ field, key }) => (
                      <span key={key} className="flex items-center gap-0.5" title={field.label}>
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
                <div className="hidden justify-center text-xs text-gray-700 sm:flex dark:text-gray-200">
                  {book.year || '-'}
                </div>

                {/* Universal mode: Display fields as separate columns - Desktop only */}
                {searchMode === 'universal' && (
                  <>
                    {/* Rating column */}
                    <div className="hidden justify-start sm:flex">
                      {ratingField ? (
                        <DisplayFieldBadge field={ratingField} />
                      ) : (
                        <span className="text-xs text-gray-500">-</span>
                      )}
                    </div>
                    {/* Length column */}
                    <div className="hidden justify-start sm:flex">
                      {lengthField ? (
                        <DisplayFieldBadge field={lengthField} />
                      ) : (
                        <span className="text-xs text-gray-500">-</span>
                      )}
                    </div>
                    {/* Narrator column */}
                    <div className="hidden justify-start sm:flex">
                      {narratorField ? (
                        <DisplayFieldBadge field={narratorField} />
                      ) : (
                        <span className="text-xs text-gray-500">-</span>
                      )}
                    </div>
                  </>
                )}

                {/* Direct mode: Language Badge - Desktop only */}
                {searchMode !== 'universal' && (
                  <div className="hidden justify-center sm:flex">
                    <span
                      className={`${languageColor.bg} ${languageColor.text} rounded-lg px-2 py-0.5 text-[11px] font-semibold tracking-wide uppercase`}
                      title={book.language || 'Unknown'}
                    >
                      {book.language || '-'}
                    </span>
                  </div>
                )}

                {/* Direct mode: Format Badge - Desktop only */}
                {searchMode !== 'universal' && (
                  <div className="hidden justify-center sm:flex">
                    <span
                      className={`${formatColor.bg} ${formatColor.text} rounded-lg px-2 py-0.5 text-[11px] font-semibold tracking-wide uppercase`}
                      title={book.format || 'Unknown'}
                    >
                      {book.format || '-'}
                    </span>
                  </div>
                )}

                {/* Direct mode: Size - Desktop only */}
                {searchMode !== 'universal' && (
                  <div className="hidden justify-center text-xs text-gray-700 sm:flex dark:text-gray-200">
                    {book.size || '-'}
                  </div>
                )}

                {/* Direct mode: Downloads - Desktop only */}
                {searchMode !== 'universal' && (
                  <div className="hidden justify-center text-xs text-gray-700 sm:flex dark:text-gray-200">
                    {(() => {
                      const d = getDownloadsCount(book);
                      const s = getStarCount(book);
                      if (d == null || d <= 0) return '-';
                      const text =
                        s != null && s > 0 ? `${d.toLocaleString()}/⭐${s}` : d.toLocaleString();
                      return text;
                    })()}
                  </div>
                )}

                {/* Action Buttons */}
                <div className="flex flex-row justify-end gap-0.5 sm:gap-1 sm:pr-3">
                  {bookSupportsTargets(book) && targetProvider && targetBookId && (
                    <BookTargetDropdown
                      provider={targetProvider}
                      bookId={targetBookId}
                      onShowToast={onShowToast}
                      variant="icon"
                      onOpenChange={(isOpen) => setOpenDropdownBookId(isOpen ? book.id : null)}
                    />
                  )}
                  <button
                    type="button"
                    className="hover-action flex items-center justify-center rounded-full p-1.5 text-gray-600 transition-all duration-200 sm:p-2 dark:text-gray-200"
                    onClick={() => {
                      void handleDetails(book.id);
                    }}
                    disabled={isLoadingDetails}
                    aria-label={`View details for ${book.title || 'this book'}`}
                  >
                    {isLoadingDetails ? (
                      <div className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent sm:h-5 sm:w-5" />
                    ) : (
                      <svg
                        className="h-4 w-4 sm:h-5 sm:w-5"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                        strokeWidth="1.5"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M13 16h-1v-4h-1m1-4h.01M12 20a8 8 0 100-16 8 8 0 000 16z"
                        />
                      </svg>
                    )}
                  </button>
                  <BookActionButton
                    book={book}
                    buttonState={buttonState}
                    onDownload={onDownload}
                    onGetReleases={(selectedBook) => {
                      void handleGetReleases(selectedBook);
                    }}
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

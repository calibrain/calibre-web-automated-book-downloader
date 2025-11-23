import { useState } from 'react';
import { Book, ButtonStateInfo } from '../../types';
import { BookDownloadButton } from '../BookDownloadButton';

interface ListViewProps {
  books: Book[];
  onDetails: (id: string) => Promise<void>;
  onDownload: (book: Book) => Promise<void>;
  getButtonState: (bookId: string) => ButtonStateInfo;
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

const getLanguageColor = (language?: string): string => {
  if (!language || language === '-') return 'bg-gray-400 dark:bg-gray-600';
  const lang = language.toLowerCase();
  const colorMap: Record<string, string> = {
    en: 'bg-blue-500 dark:bg-blue-600',
    english: 'bg-blue-500 dark:bg-blue-600',
    es: 'bg-orange-500 dark:bg-orange-600',
    spanish: 'bg-orange-500 dark:bg-orange-600',
    fr: 'bg-purple-500 dark:bg-purple-600',
    french: 'bg-purple-500 dark:bg-purple-600',
    de: 'bg-yellow-500 dark:bg-yellow-600',
    german: 'bg-yellow-500 dark:bg-yellow-600',
    it: 'bg-green-500 dark:bg-green-600',
    italian: 'bg-green-500 dark:bg-green-600',
    pt: 'bg-teal-500 dark:bg-teal-600',
    portuguese: 'bg-teal-500 dark:bg-teal-600',
    ru: 'bg-red-500 dark:bg-red-600',
    russian: 'bg-red-500 dark:bg-red-600',
    ja: 'bg-pink-500 dark:bg-pink-600',
    japanese: 'bg-pink-500 dark:bg-pink-600',
    zh: 'bg-rose-500 dark:bg-rose-600',
    chinese: 'bg-rose-500 dark:bg-rose-600',
  };
  return colorMap[lang] || 'bg-indigo-500 dark:bg-indigo-600';
};

const getFormatColor = (format?: string): string => {
  if (!format || format === '-') return 'bg-gray-400 dark:bg-gray-600';
  const fmt = format.toLowerCase();
  const colorMap: Record<string, string> = {
    pdf: 'bg-red-500 dark:bg-red-600',
    epub: 'bg-green-500 dark:bg-green-600',
    mobi: 'bg-blue-500 dark:bg-blue-600',
    azw3: 'bg-purple-500 dark:bg-purple-600',
    txt: 'bg-gray-500 dark:bg-gray-600',
    djvu: 'bg-orange-500 dark:bg-orange-600',
    fb2: 'bg-teal-500 dark:bg-teal-600',
    cbr: 'bg-yellow-500 dark:bg-yellow-600',
    cbz: 'bg-amber-500 dark:bg-amber-600',
  };
  return colorMap[fmt] || 'bg-cyan-500 dark:bg-cyan-600';
};

export const ListView = ({ books, onDetails, onDownload, getButtonState }: ListViewProps) => {
  const [detailsLoadingId, setDetailsLoadingId] = useState<string | null>(null);

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
          const buttonState = getButtonState(book.id);
          const isLoadingDetails = detailsLoadingId === book.id;

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
              <div className="grid grid-cols-[auto_minmax(0,1fr)_auto_auto] sm:grid-cols-[auto_minmax(0,2fr)_minmax(50px,0.25fr)_minmax(60px,0.3fr)_minmax(60px,0.3fr)_minmax(60px,0.3fr)_auto] items-center gap-2 sm:gap-y-1 sm:gap-x-0.5 w-full">
                {/* Thumbnail */}
                <div className="flex items-center pl-1 sm:pl-3">
                  <ListViewThumbnail preview={book.preview} title={book.title} />
                </div>

                {/* Title and Author */}
                <div className="min-w-0 flex flex-col justify-center sm:pl-3">
                  <h3 className="font-semibold text-xs min-[400px]:text-sm sm:text-base leading-tight line-clamp-1 sm:line-clamp-2" title={book.title || 'Untitled'}>
                    {book.title || 'Untitled'}
                  </h3>
                  <p className="text-[10px] min-[400px]:text-xs sm:text-sm text-gray-600 dark:text-gray-300 truncate">
                    {book.author || 'Unknown author'}
                    {book.year && <span className="sm:hidden"> • {book.year}</span>}
                  </p>
                </div>

                {/* Format and Size - Mobile only */}
                <div className="flex sm:hidden flex-col items-end text-[10px] opacity-70 leading-tight">
                  <span>{book.format || '-'}</span>
                  {book.size && <span>{book.size}</span>}
                </div>

                {/* Year - Desktop only */}
                <div className="hidden sm:flex text-xs text-gray-700 dark:text-gray-200 justify-center">
                  {book.year || '-'}
                </div>

                {/* Language Badge - Desktop only */}
                <div className="hidden sm:flex justify-center">
                  <span
                    className={`${getLanguageColor(book.language)} text-white text-[11px] font-semibold px-2 py-0.5 rounded uppercase tracking-wide`}
                    title={book.language || 'Unknown'}
                  >
                    {book.language || '-'}
                  </span>
                </div>

                {/* Format Badge - Desktop only */}
                <div className="hidden sm:flex justify-center">
                  <span
                    className={`${getFormatColor(book.format)} text-white text-[11px] font-semibold px-2 py-0.5 rounded uppercase tracking-wide`}
                    title={book.format || 'Unknown'}
                  >
                    {book.format || '-'}
                  </span>
                </div>

                {/* Size - Desktop only */}
                <div className="hidden sm:flex text-xs text-gray-700 dark:text-gray-200 justify-center">
                  {book.size || '-'}
                </div>

                {/* Action Buttons */}
                <div className="flex flex-row justify-end gap-0.5 sm:gap-1">
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
                  <BookDownloadButton
                    buttonState={buttonState}
                    onDownload={() => onDownload(book)}
                    variant="icon"
                    size="md"
                    ariaLabel={buttonState.text}
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


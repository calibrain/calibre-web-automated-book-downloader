import { useState, useEffect } from 'react';
import { Book, ButtonStateInfo } from '../types';

const SkeletonLoader = () => (
  <div className="w-full h-full bg-gradient-to-r from-gray-300 via-gray-200 to-gray-300 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 animate-pulse" />
);

interface BookCardProps {
  book: Book;
  onDetails: (id: string) => Promise<void>;
  onDownload: (book: Book) => Promise<void>;
  buttonState: ButtonStateInfo;
}

export const BookCard = ({ book, onDetails, onDownload, buttonState }: BookCardProps) => {
  const [isQueuing, setIsQueuing] = useState(false);
  const [isLoadingDetails, setIsLoadingDetails] = useState(false);
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);

  // Clear queuing state once button state changes from download
  useEffect(() => {
    if (isQueuing && buttonState.state !== 'download') {
      setIsQueuing(false);
    }
  }, [buttonState.state, isQueuing]);

  const isDisabled = buttonState.state !== 'download' || isQueuing;
  const displayText = isQueuing ? 'Queuing...' : buttonState.text;
  const isQueued = buttonState.state === 'queued' || buttonState.state === 'downloading';

  const handleDetails = async (id: string) => {
    setIsLoadingDetails(true);
    try {
      await onDetails(id);
    } finally {
      setIsLoadingDetails(false);
    }
  };

  const handleDownload = async () => {
    setIsQueuing(true);
    try {
      await onDownload(book);
    } catch (error) {
      setIsQueuing(false);
    }
  };

  return (
    <article
      className="book-card overflow-hidden flex flex-col sm:flex-col max-sm:flex-row space-between w-full sm:max-w-[292px] sm:max-h-[590px] max-sm:h-[180px]"
      style={{ 
        background: 'var(--bg-soft)',
        borderRadius: '.75rem'
      }}
    >
      {/* Book Cover Image - 2:3 aspect ratio on desktop, fixed width on mobile */}
      <div 
        className="relative w-full sm:w-full max-sm:w-[120px] max-sm:h-full max-sm:flex-shrink-0" 
        style={{ aspectRatio: '2/3' }}
      >
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
                objectPosition: 'top'
              }}
              onLoad={() => setImageLoaded(true)}
              onError={() => setImageError(true)}
            />
          </>
        ) : (
          <div
            className="w-full h-full flex items-center justify-center text-sm opacity-50"
            style={{ background: 'var(--border-muted)' }}
          >
            No Cover
          </div>
        )}
      </div>

      {/* Book Details Section */}
      <div className="p-4 max-sm:p-3 max-sm:py-2 flex flex-col gap-3 max-sm:gap-2 max-sm:flex-1 max-sm:justify-between">
        <div className="space-y-1 max-sm:space-y-0.5">
          <h3 
            className="font-semibold leading-tight truncate text-base max-sm:text-sm" 
            title={book.title || 'Untitled'}
          >
            {book.title || 'Untitled'}
          </h3>
          <p className="text-sm max-sm:text-xs opacity-80 truncate">{book.author || 'Unknown author'}</p>
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
        </div>
        
        {/* Action Buttons */}
        <div className="flex gap-2 max-sm:gap-1.5">
        <button
          className="px-3 py-2 max-sm:px-2 max-sm:py-1.5 rounded border text-sm max-sm:text-xs flex-1 flex items-center justify-center gap-2 max-sm:gap-1"
          onClick={() => handleDetails(book.id)}
          style={{ borderColor: 'var(--border-muted)' }}
          disabled={isLoadingDetails}
        >
          <span className="details-button-text">
            {isLoadingDetails ? 'Loading' : 'Details'}
          </span>
          <div
            className={`details-spinner w-4 h-4 max-sm:w-3 max-sm:h-3 border-2 border-current border-t-transparent rounded-full ${
              isLoadingDetails ? '' : 'hidden'
            }`}
          />
        </button>
        <button
          className={`px-3 py-2 max-sm:px-2 max-sm:py-1.5 rounded text-white text-sm max-sm:text-xs flex-1 flex items-center justify-center gap-2 max-sm:gap-1 ${
            isQueued
              ? 'bg-gray-500 cursor-not-allowed opacity-75'
              : buttonState.state !== 'download'
              ? 'bg-green-600 hover:bg-green-700 disabled:opacity-60 disabled:cursor-not-allowed'
              : 'bg-blue-600 hover:bg-blue-700'
          }`}
          onClick={handleDownload}
          disabled={isDisabled || isQueued}
          data-action="download"
        >
          <span className="download-button-text">{isQueued ? 'Queued' : displayText}</span>
          <div
            className={`download-spinner w-4 h-4 max-sm:w-3 max-sm:h-3 border-2 border-white border-t-transparent rounded-full ${
              buttonState.state !== 'download' || isQueuing ? '' : 'hidden'
            }`}
          />
        </button>
        </div>
      </div>
    </article>
  );
};

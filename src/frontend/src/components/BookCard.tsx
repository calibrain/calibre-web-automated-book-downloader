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
  const [isHovered, setIsHovered] = useState(false);

  // Clear queuing state once button state changes from download
  useEffect(() => {
    if (isQueuing && buttonState.state !== 'download') {
      setIsQueuing(false);
    }
  }, [buttonState.state, isQueuing]);

  const isCompleted = buttonState.state === 'completed';
  const hasError = buttonState.state === 'error';
  const isDisabled = buttonState.state !== 'download' || isQueuing || isCompleted;
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
      className="book-card overflow-hidden flex flex-col sm:flex-col max-sm:flex-row space-between w-full sm:max-w-[292px] sm:max-h-[590px] max-sm:h-[180px] transition-shadow duration-300"
      style={{ 
        background: 'var(--bg-soft)',
        borderRadius: '.75rem',
        boxShadow: isHovered ? '0 10px 30px rgba(0, 0, 0, 0.15)' : 'none'
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Book Cover Image - 2:3 aspect ratio on desktop, fixed width on mobile */}
      <div 
        className="relative w-full sm:w-full max-sm:w-[120px] max-sm:h-full max-sm:flex-shrink-0 group" 
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
        
        {/* Hover overlay with 2% white opacity */}
        <div 
          className="absolute inset-0 bg-white transition-opacity duration-300 pointer-events-none"
          style={{ opacity: isHovered ? 0.02 : 0 }}
        />
        
        {/* Info button - appears on hover, positioned bottom-right */}
        <button
          className="absolute bottom-2 right-2 w-8 h-8 rounded-full bg-white/90 dark:bg-gray-800/90 backdrop-blur-sm flex items-center justify-center transition-all duration-300 shadow-lg hover:scale-110 max-sm:hidden"
          style={{ 
            opacity: isHovered ? 1 : 0,
            pointerEvents: isHovered ? 'auto' : 'none'
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
            <svg 
              className="w-4 h-4" 
              fill="none" 
              stroke="currentColor" 
              viewBox="0 0 24 24"
            >
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

      {/* Book Details Section */}
      <div className="p-4 max-sm:p-3 max-sm:py-2 flex flex-col gap-3 max-sm:gap-2 max-sm:flex-1 max-sm:justify-between max-sm:min-w-0 sm:flex-1 sm:flex sm:flex-col">
        <div className="space-y-1 max-sm:space-y-0.5 max-sm:min-w-0">
          <h3 
            className="font-semibold leading-tight truncate text-base max-sm:text-sm max-sm:min-w-0" 
            title={book.title || 'Untitled'}
          >
            {book.title || 'Untitled'}
          </h3>
          <p className="text-sm max-sm:text-xs opacity-80 truncate max-sm:min-w-0">{book.author || 'Unknown author'}</p>
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
        
        {/* Mobile: Details and Download buttons side by side */}
        <div className="flex gap-1.5 sm:hidden">
          <button
            className="px-2 py-1.5 rounded border text-xs flex-1 flex items-center justify-center gap-1"
            onClick={() => handleDetails(book.id)}
            style={{ borderColor: 'var(--border-muted)' }}
            disabled={isLoadingDetails}
          >
            <span className="details-button-text">
              {isLoadingDetails ? 'Loading' : 'Details'}
            </span>
            <div
              className={`details-spinner w-3 h-3 border-2 border-current border-t-transparent rounded-full ${
                isLoadingDetails ? '' : 'hidden'
              }`}
            />
          </button>
          <button
            className={`px-2 py-1.5 rounded text-white text-xs flex-1 flex items-center justify-center gap-1 ${
              isCompleted
                ? 'bg-green-600 cursor-not-allowed'
                : hasError
                ? 'bg-red-600 cursor-not-allowed opacity-75'
                : isQueued
                ? 'bg-gray-500 cursor-not-allowed opacity-75'
                : buttonState.state !== 'download'
                ? 'bg-green-600 hover:bg-green-700 disabled:opacity-60 disabled:cursor-not-allowed'
                : 'bg-sky-700 hover:bg-sky-800'
            }`}
            onClick={handleDownload}
            disabled={isDisabled || isQueued}
            data-action="download"
          >
            <span className="download-button-text">{isQueued ? 'Queued' : displayText}</span>
            {isCompleted && (
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            )}
            {hasError && (
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            )}
            <div
              className={`download-spinner w-3 h-3 border-2 border-white border-t-transparent rounded-full ${
                (buttonState.state !== 'download' || isQueuing) && !isCompleted && !hasError ? '' : 'hidden'
              }`}
            />
          </button>
        </div>
      </div>
      
      {/* Desktop: Full-width Download button at bottom */}
      <button
        className={`hidden sm:flex w-full px-4 py-3 text-white text-sm items-center justify-center gap-2 ${
          isCompleted
            ? 'bg-green-600 cursor-not-allowed'
            : hasError
            ? 'bg-red-600 cursor-not-allowed opacity-75'
            : isQueued
            ? 'bg-gray-500 cursor-not-allowed opacity-75'
            : buttonState.state !== 'download'
            ? 'bg-green-600 hover:bg-green-700 disabled:opacity-60 disabled:cursor-not-allowed'
            : 'bg-sky-700 hover:bg-sky-800'
        }`}
        onClick={handleDownload}
        disabled={isDisabled || isQueued}
        data-action="download"
        style={{
          borderBottomLeftRadius: '.75rem',
          borderBottomRightRadius: '.75rem'
        }}
      >
        <span className="download-button-text">{isQueued ? 'Queued' : displayText}</span>
        {isCompleted && (
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
        )}
        {hasError && (
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        )}
        <div
          className={`download-spinner w-4 h-4 border-2 border-white border-t-transparent rounded-full ${
            (buttonState.state !== 'download' || isQueuing) && !isCompleted && !hasError ? '' : 'hidden'
          }`}
        />
      </button>
    </article>
  );
};

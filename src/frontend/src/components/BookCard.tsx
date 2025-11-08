import { useState, useEffect } from 'react';
import { Book, ButtonStateInfo } from '../types';

interface BookCardProps {
  book: Book;
  onDetails: (id: string) => Promise<void>;
  onDownload: (book: Book) => Promise<void>;
  buttonState: ButtonStateInfo;
}

export const BookCard = ({ book, onDetails, onDownload, buttonState }: BookCardProps) => {
  const [isQueuing, setIsQueuing] = useState(false);
  const [isLoadingDetails, setIsLoadingDetails] = useState(false);

  // Clear queuing state once button state changes from download
  useEffect(() => {
    if (isQueuing && buttonState.state !== 'download') {
      setIsQueuing(false);
    }
  }, [buttonState.state, isQueuing]);

  const isDisabled = buttonState.state !== 'download' || isQueuing;
  const displayText = isQueuing ? 'Queuing...' : buttonState.text;

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
      className="book-card rounded border p-3 flex flex-col gap-3"
      style={{ borderColor: 'var(--border-muted)', background: 'var(--bg-soft)' }}
    >
      <div className="book-card-content flex flex-col gap-3">
        {book.preview ? (
          <img
            src={book.preview}
            alt="Cover"
            className="book-card-cover w-full h-88 object-cover rounded"
          />
        ) : (
          <div
            className="book-card-cover w-full h-88 rounded flex items-center justify-center opacity-70"
            style={{ background: 'var(--bg-soft)' }}
          >
            No Cover
          </div>
        )}
        <div className="book-card-text flex-1 space-y-1">
          <h3 className="font-semibold leading-tight">{book.title || 'Untitled'}</h3>
          <p className="text-sm opacity-80">{book.author || 'Unknown author'}</p>
          <div className="text-xs opacity-70 flex flex-wrap gap-2">
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
      </div>
      <div className="book-card-buttons flex gap-2">
        <button
          className="px-3 py-2 rounded border text-sm flex-1 flex items-center justify-center gap-2"
          onClick={() => handleDetails(book.id)}
          style={{ borderColor: 'var(--border-muted)' }}
          disabled={isLoadingDetails}
        >
          <span className="details-button-text">
            {isLoadingDetails ? 'Loading' : 'Details'}
          </span>
          <div
            className={`details-spinner w-4 h-4 border-2 border-current border-t-transparent rounded-full ${
              isLoadingDetails ? '' : 'hidden'
            }`}
          />
        </button>
        <button
          className={`px-3 py-2 rounded text-white text-sm flex-1 flex items-center justify-center gap-2 ${
            buttonState.state !== 'download'
              ? 'bg-green-600 hover:bg-green-700 disabled:opacity-60 disabled:cursor-not-allowed'
              : 'bg-blue-600 hover:bg-blue-700'
          }`}
          onClick={handleDownload}
          disabled={isDisabled}
          data-action="download"
        >
          <span className="download-button-text">{displayText}</span>
          <div
            className={`download-spinner w-4 h-4 border-2 border-white border-t-transparent rounded-full ${
              buttonState.state !== 'download' || isQueuing ? '' : 'hidden'
            }`}
          />
        </button>
      </div>
    </article>
  );
};

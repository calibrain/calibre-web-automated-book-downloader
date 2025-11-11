import { useState, useEffect } from 'react';
import { Book, ButtonStateInfo } from '../types';
import { CircularProgress } from './CircularProgress';

interface DetailsModalProps {
  book: Book | null;
  onClose: () => void;
  onDownload: (book: Book) => Promise<void>;
  buttonState: ButtonStateInfo;
}

export const DetailsModal = ({ book, onClose, onDownload, buttonState }: DetailsModalProps) => {
  const [isQueuing, setIsQueuing] = useState(false);

  // Clear queuing state and close modal once button state changes from download
  useEffect(() => {
    if (isQueuing && buttonState.state !== 'download') {
      setIsQueuing(false);
      // Close modal after status has updated
      const timer = setTimeout(onClose, 500);
      return () => clearTimeout(timer);
    }
  }, [buttonState.state, isQueuing, onClose]);

  // Handle ESC key to close modal
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [onClose]);

  if (!book) return null;

  const isCompleted = buttonState.state === 'completed';
  const hasError = buttonState.state === 'error';
  const isInProgress = ['queued', 'resolving', 'bypassing', 'downloading', 'verifying', 'ingesting'].includes(buttonState.state);
  const isDisabled = buttonState.state !== 'download' || isQueuing || isCompleted;
  const displayText = isQueuing ? 'Queuing...' : buttonState.text;

  // Show circular progress only for downloading state with progress data
  const showCircularProgress = buttonState.state === 'downloading' && buttonState.progress !== undefined;
  // Show spinner for other in-progress states or when queuing
  const showSpinner = (isInProgress && !showCircularProgress) || isQueuing;

  const handleDownload = async () => {
    setIsQueuing(true);
    try {
      await onDownload(book);
      // Don't close here - wait for button state to change
    } catch (error) {
      setIsQueuing(false);
      // Close on error
      setTimeout(onClose, 300);
    }
  };

  return (
    <div
      className="modal-overlay active"
      onClick={e => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="details-container">
        <div className="p-4 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              {book.preview && (
                <img
                  src={book.preview}
                  alt="Cover"
                  className="w-full h-88 object-cover rounded"
                />
              )}
            </div>
            <div>
              <h3 className="text-lg font-semibold mb-1">{book.title || 'Untitled'}</h3>
              <p className="text-sm opacity-80">{book.author || 'Unknown author'}</p>
              <div className="text-sm mt-2 space-y-1">
                <p>
                  <strong>Publisher:</strong> {book.publisher || '-'}
                </p>
                <p>
                  <strong>Year:</strong> {book.year || '-'}
                </p>
                <p>
                  <strong>Language:</strong> {book.language || '-'}
                </p>
                <p>
                  <strong>Format:</strong> {book.format || '-'}
                </p>
                <p>
                  <strong>Size:</strong> {book.size || '-'}
                </p>
              </div>
            </div>
          </div>
          {book.info && Object.keys(book.info).length > 0 && (
            <div>
              <h4 className="font-semibold mb-2">Further Information</h4>
              <ul className="list-disc pl-6 space-y-1 text-sm">
                {Object.entries(book.info).map(([k, v]) => (
                  <li key={k}>
                    <strong>{k}:</strong>{' '}
                    {Array.isArray(v) ? v.join(', ') : v}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="flex gap-2">
            <button
              id="download-button"
              data-id={book.id}
              className={`px-3 py-2 rounded text-white text-sm flex items-center justify-center gap-2 ${
                isCompleted
                  ? 'bg-green-600 cursor-not-allowed'
                  : hasError
                  ? 'bg-red-600 cursor-not-allowed opacity-75'
                  : isInProgress
                  ? 'bg-gray-500 cursor-not-allowed opacity-75'
                  : 'bg-blue-600 hover:bg-blue-700'
              }`}
              onClick={handleDownload}
              disabled={isDisabled || isInProgress}
            >
              <span className="download-button-text">{displayText}</span>
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
              {showCircularProgress && <CircularProgress progress={buttonState.progress} size={16} />}
              {showSpinner && (
                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              )}
            </button>
            <button
              id="close-details"
              className="px-3 py-2 rounded border text-sm"
              style={{ borderColor: 'var(--border-muted)' }}
              onClick={onClose}
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

import { useState, useEffect } from 'react';
import { Book, ButtonStateInfo } from '../types';

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

  if (!book) return null;

  const isDisabled = buttonState.state !== 'download' || isQueuing;
  const displayText = isQueuing ? 'Queuing...' : buttonState.text;

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
                buttonState.state !== 'download'
                  ? 'bg-green-600 hover:bg-green-700 disabled:opacity-60 disabled:cursor-not-allowed'
                  : 'bg-blue-600 hover:bg-blue-700'
              }`}
              onClick={handleDownload}
              disabled={isDisabled}
            >
              <span className="download-button-text">{displayText}</span>
              <div
                className={`download-spinner w-4 h-4 border-2 border-white border-t-transparent rounded-full ${
                  buttonState.state !== 'download' || isQueuing ? '' : 'hidden'
                }`}
              />
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

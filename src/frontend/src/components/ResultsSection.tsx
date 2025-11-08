import { Book, ButtonStateInfo } from '../types';
import { BookCard } from './BookCard';

interface ResultsSectionProps {
  books: Book[];
  visible: boolean;
  onDetails: (id: string) => Promise<void>;
  onDownload: (book: Book) => Promise<void>;
  getButtonState: (bookId: string) => ButtonStateInfo;
}

export const ResultsSection = ({
  books,
  visible,
  onDetails,
  onDownload,
  getButtonState,
}: ResultsSectionProps) => {
  if (!visible) return null;

  return (
    <section id="results-section" className="mb-8">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-xl font-semibold">Search Results</h2>
      </div>
      <div
        id="results-grid"
        className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4"
      >
        {books.map(book => (
          <BookCard
            key={book.id}
            book={book}
            onDetails={onDetails}
            onDownload={onDownload}
            buttonState={getButtonState(book.id)}
          />
        ))}
      </div>
      {books.length === 0 && (
        <div className="mt-4 text-sm opacity-80">No results found.</div>
      )}
    </section>
  );
};

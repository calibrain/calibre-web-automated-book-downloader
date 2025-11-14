import { useState, useEffect } from 'react';
import { Book, ButtonStateInfo } from '../types';
import { BookCard } from './BookCard';
import { BookListView } from './BookListView';

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
  const [viewMode, setViewMode] = useState<'card' | 'compact' | 'list'>(() => {
    const saved = localStorage.getItem('bookViewMode');
    return saved === 'card' || saved === 'compact' || saved === 'list' ? saved : 'compact';
  });

  const [isDesktop, setIsDesktop] = useState(false);

  useEffect(() => {
    localStorage.setItem('bookViewMode', viewMode);
  }, [viewMode]);

  // Track whether we're in desktop layout (sm breakpoint and above)
  useEffect(() => {
    const checkDesktop = () => {
      setIsDesktop(window.innerWidth >= 640); // sm breakpoint
    };
    
    checkDesktop();
    window.addEventListener('resize', checkDesktop);
    return () => window.removeEventListener('resize', checkDesktop);
  }, []);

  if (!visible) return null;

  return (
    <section id="results-section" className="mb-4 sm:mb-8 w-full">
      <div className="flex items-center justify-between mb-2 sm:mb-3">
        <h2 className="text-lg sm:text-xl font-semibold animate-fade-in-up">Search Results</h2>
        
        {/* View toggle buttons - Desktop: show all 3, Mobile: show Compact and List only */}
        <div className="flex items-center gap-2">
          {isDesktop && (
            <button
              onClick={() => setViewMode('card')}
              className={`p-2 rounded-full transition-all duration-200 ${
                viewMode === 'card'
                  ? 'text-white bg-sky-700 hover:bg-sky-800'
                  : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-900 dark:text-gray-100'
              }`}
              title="Card view"
              aria-label="Card view"
              aria-pressed={viewMode === 'card'}
            >
              <svg
                className="w-5 h-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                strokeWidth="1.5"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M3.75 6A2.25 2.25 0 0 1 6 3.75h2.25A2.25 2.25 0 0 1 10.5 6v2.25a2.25 2.25 0 0 1-2.25 2.25H6a2.25 2.25 0 0 1-2.25-2.25V6ZM3.75 15.75A2.25 2.25 0 0 1 6 13.5h2.25a2.25 2.25 0 0 1 2.25 2.25V18a2.25 2.25 0 0 1-2.25 2.25H6A2.25 2.25 0 0 1 3.75 18v-2.25ZM13.5 6a2.25 2.25 0 0 1 2.25-2.25H18A2.25 2.25 0 0 1 20.25 6v2.25A2.25 2.25 0 0 1 18 10.5h-2.25a2.25 2.25 0 0 1-2.25-2.25V6ZM13.5 15.75a2.25 2.25 0 0 1 2.25-2.25H18a2.25 2.25 0 0 1 2.25 2.25V18A2.25 2.25 0 0 1 18 20.25h-2.25A2.25 2.25 0 0 1 13.5 18v-2.25Z"
                />
              </svg>
            </button>
          )}
          <button
            onClick={() => setViewMode('compact')}
            className={`p-2 rounded-full transition-all duration-200 ${
              viewMode === 'compact'
                ? 'text-white bg-sky-700 hover:bg-sky-800'
                : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-900 dark:text-gray-100'
            }`}
            title="Compact view"
            aria-label="Compact view"
            aria-pressed={viewMode === 'compact'}
          >
            <svg
              className="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
            >
              <rect x="3.75" y="4.5" width="6" height="6" rx="1.125" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 6h8.25M12 8.25h6" />
              <rect x="3.75" y="13.5" width="6" height="6" rx="1.125" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 15h8.25M12 17.25h6" />
            </svg>
          </button>
          <button
            onClick={() => setViewMode('list')}
            className={`p-2 rounded-full transition-all duration-200 ${
              viewMode === 'list'
                ? 'text-white bg-sky-700 hover:bg-sky-800'
                : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-900 dark:text-gray-100'
            }`}
            title="List view"
            aria-label="List view"
            aria-pressed={viewMode === 'list'}
          >
            <svg
              className="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0ZM3.75 12h.007v.008H3.75V12Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm-.375 5.25h.007v.008H3.75v-.008Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z"
              />
            </svg>
          </button>
        </div>
      </div>
      {viewMode === 'list' ? (
        <BookListView
          books={books}
          onDetails={onDetails}
          onDownload={onDownload}
          getButtonState={getButtonState}
        />
      ) : (
        <div
          id="results-grid"
          className={`grid gap-8 ${
            !isDesktop
              ? 'grid-cols-1 items-start'
              : viewMode === 'card'
              ? 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 items-stretch'
              : 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-2 xl:grid-cols-3 items-start'
          }`}
        >
          {books.map((book, index) => {
            // Desktop: use selected viewMode. Mobile: always compact
            const effectiveVariant: 'card' | 'compact' = isDesktop
              ? viewMode
              : 'compact';
            
            return (
              <div
                key={book.id}
                className="animate-slide-up"
                style={{
                  animationDelay: `${index * 50}ms`,
                  animationFillMode: 'both',
                }}
              >
                <BookCard
                  book={book}
                  onDetails={onDetails}
                  onDownload={onDownload}
                  buttonState={getButtonState(book.id)}
                  variant={effectiveVariant}
                  showDetailsButton={!isDesktop}
                />
              </div>
            );
          })}
        </div>
      )}
      {books.length === 0 && (
        <div className="mt-4 text-sm opacity-80">No results found.</div>
      )}
    </section>
  );
};

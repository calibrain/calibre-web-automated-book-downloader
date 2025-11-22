import { useState, useEffect } from 'react';
import { Book, ButtonStateInfo } from '../types';
import { CardView } from './resultsViews/CardView';
import { CompactView } from './resultsViews/CompactView';
import { ListView } from './resultsViews/ListView';
import { Dropdown } from './Dropdown';
import { SORT_OPTIONS } from '../data/filterOptions';

interface ResultsSectionProps {
  books: Book[];
  visible: boolean;
  onDetails: (id: string) => Promise<void>;
  onDownload: (book: Book) => Promise<void>;
  getButtonState: (bookId: string) => ButtonStateInfo;
  sortValue: string;
  onSortChange: (value: string) => void;
}

export const ResultsSection = ({
  books,
  visible,
  onDetails,
  onDownload,
  getButtonState,
  sortValue,
  onSortChange,
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
      <div className="flex items-center justify-between mb-2 sm:mb-3 relative z-10">
        <SortControl value={sortValue} onChange={onSortChange} />
        
        {/* View toggle buttons - Desktop: show all 3, Mobile: show Compact and List only */}
        <div className="flex items-center gap-2">
          {isDesktop && (
            <button
              onClick={() => setViewMode('card')}
              className={`p-2 rounded-full transition-all duration-200 ${
                viewMode === 'card'
                  ? 'text-white bg-sky-700 hover:bg-sky-800'
                  : 'hover-action text-gray-900 dark:text-gray-100'
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
                : 'hover-action text-gray-900 dark:text-gray-100'
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
                : 'hover-action text-gray-900 dark:text-gray-100'
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
        <ListView books={books} onDetails={onDetails} onDownload={onDownload} getButtonState={getButtonState} />
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
            const shouldUseCardLayout = isDesktop && viewMode === 'card';

            const animationDelay = index * 50;

            return shouldUseCardLayout ? (
              <CardView
                key={book.id}
                book={book}
                onDetails={onDetails}
                onDownload={onDownload}
                buttonState={getButtonState(book.id)}
                animationDelay={animationDelay}
              />
            ) : (
              <CompactView
                key={book.id}
                book={book}
                onDetails={onDetails}
                onDownload={onDownload}
                buttonState={getButtonState(book.id)}
                showDetailsButton={!isDesktop}
                animationDelay={animationDelay}
              />
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

interface SortControlProps {
  value: string;
  onChange: (value: string) => void;
}

const SortControl = ({ value, onChange }: SortControlProps) => {
  const selected = SORT_OPTIONS.find(option => option.value === value) ?? SORT_OPTIONS[0];

  return (
    <Dropdown
      align="left"
      widthClassName="w-60 sm:w-72"
      renderTrigger={({ isOpen, toggle }) => (
        <button
          type="button"
          onClick={toggle}
          className={`relative flex items-center gap-2 px-3 py-2 rounded-full transition-all duration-200 text-gray-900 dark:text-gray-100 hover-action ${
            isOpen ? 'bg-gray-100 dark:bg-gray-700' : ''
          } animate-fade-in-up`}
          aria-haspopup="listbox"
          aria-expanded={isOpen}
          aria-label="Change sort order"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.5}
            stroke="currentColor"
            className="w-5 h-5 sm:w-6 sm:h-6"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M3 7.5 7.5 3m0 0L12 7.5M7.5 3v13.5m13.5 0L16.5 21m0 0L12 16.5m4.5 4.5V7.5"
            />
          </svg>
          <span className="text-sm font-medium whitespace-nowrap">{selected.label}</span>
        </button>
      )}
    >
      {({ close }) => (
        <div role="listbox" aria-label="Sort results">
          {SORT_OPTIONS.map(option => {
            const isSelected = option.value === selected.value;
            return (
              <button
                type="button"
                key={option.value || 'default'}
                className={`w-full px-3 py-2 text-left text-base flex items-center justify-between gap-2 hover-surface ${
                  isSelected ? 'text-sky-600 dark:text-sky-300 font-medium' : ''
                }`}
                onClick={() => {
                  onChange(option.value);
                  close();
                }}
                role="option"
                aria-selected={isSelected}
              >
                <span>{option.label}</span>
                {isSelected && (
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    fill="none"
                    viewBox="0 0 24 24"
                    strokeWidth={1.5}
                    stroke="currentColor"
                    className="w-4 h-4"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                  </svg>
                )}
              </button>
            );
          })}
        </div>
      )}
    </Dropdown>
  );
};

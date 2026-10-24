import { useState } from 'react';

import { useSearchMode } from '../contexts/SearchModeContext';
import { SORT_OPTIONS } from '../data/filterOptions';
import { useMediaQuery } from '../hooks/useMediaQuery';
import { t } from '../i18n';
import type { Book, ButtonStateInfo, SortOption } from '../types';
import { Dropdown } from './Dropdown';
import { CardView } from './resultsViews/CardView';
import { CompactView } from './resultsViews/CompactView';
import { ListView } from './resultsViews/ListView';

// Grid layout classes by view mode
const GRID_CLASSES = {
  mobile: 'grid-cols-1 items-start',
  card: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 items-stretch',
  compact: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-2 xl:grid-cols-3 items-start',
} as const;

interface ResultsSectionProps {
  books: Book[];
  visible: boolean;
  onDetails: (id: string) => Promise<void>;
  onDownload: (book: Book) => Promise<void>;
  onGetReleases: (book: Book) => Promise<void>;
  getButtonState: (bookId: string) => ButtonStateInfo;
  getUniversalButtonState: (bookId: string) => ButtonStateInfo;
  sortValue: string;
  onSortChange: (value: string) => void;
  metadataSortOptions?: SortOption[];
  showSortControl?: boolean;
  // Pagination (universal mode)
  hasMore?: boolean;
  isLoadingMore?: boolean;
  onLoadMore?: () => void;
  totalFound?: number;
  // Direct mode: total result count from release sources
  directTotalResults?: number | string | null;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  resultsSourceUrl?: string;
}

export const ResultsSection = ({
  books,
  visible,
  onDetails,
  onDownload,
  onGetReleases,
  getButtonState,
  getUniversalButtonState,
  sortValue,
  onSortChange,
  metadataSortOptions,
  showSortControl = true,
  hasMore,
  isLoadingMore,
  onLoadMore,
  totalFound,
  directTotalResults,
  onShowToast,
  resultsSourceUrl,
}: ResultsSectionProps) => {
  const { searchMode } = useSearchMode();
  const activeViewClasses =
    searchMode === 'universal'
      ? 'bg-emerald-600 text-white hover:bg-emerald-700'
      : 'bg-sky-700 text-white hover:bg-sky-800';
  const [viewMode, setViewMode] = useState<'card' | 'compact' | 'list'>(() => {
    if (typeof window === 'undefined') {
      return 'compact';
    }

    try {
      const saved = window.localStorage.getItem('bookViewMode');
      return saved === 'card' || saved === 'compact' || saved === 'list' ? saved : 'compact';
    } catch {
      return 'compact';
    }
  });
  const isDesktop = useMediaQuery('(min-width: 640px)');

  const updateViewMode = (nextViewMode: 'card' | 'compact' | 'list') => {
    setViewMode(nextViewMode);
    try {
      window.localStorage.setItem('bookViewMode', nextViewMode);
    } catch {
      // Best effort only.
    }
  };

  if (!visible) return null;

  return (
    <section id="results-section" className="mb-4 w-full sm:mb-8">
      <div className="relative z-10 mb-2 flex items-center sm:mb-3">
        {/* Left: Sort control or Hardcover link */}
        <div>
          {showSortControl && (
            <SortControl
              value={sortValue}
              onChange={onSortChange}
              metadataSortOptions={metadataSortOptions}
            />
          )}
          {!showSortControl && resultsSourceUrl && (
            <a
              href={resultsSourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="animate-pop-up inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-gray-600 transition-colors hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-200"
            >
              {t('View list on Hardcover')}
              <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                />
              </svg>
            </a>
          )}
        </div>

        {/* Center: Results count */}
        <div className="flex flex-1 justify-center">
          {(() => {
            const count = directTotalResults ?? null;
            if (count === null || count === 0) return null;
            const isCapped = directTotalResults === '500+';
            const totalCount = isCapped ? 500 : Number(count);
            // AA-style: show page range + total
            if (totalCount === 1) {
              return (
                <span className="mx-2 mt-4 px-2 text-xs whitespace-nowrap text-gray-500 dark:text-gray-400">
                  {t('Result 1 (1 Total)')}
                </span>
              );
            }
            const shownEnd = Math.min(totalCount, 50);
            const totalStr = isCapped ? '500+' : String(totalCount);
            return (
              <span className="mx-2 mt-4 px-2 text-xs whitespace-nowrap text-gray-500 dark:text-gray-400">
                {t('Results 1-{end} ({total} Total)', { end: shownEnd, total: totalStr })}
              </span>
            );
          })()}
        </div>

        {/* Right: View toggle buttons - Desktop: show all 3, Mobile: show Compact and List only */}
        <div className="ml-auto flex items-center gap-2">
          {isDesktop && (
            <button
              type="button"
              onClick={() => updateViewMode('card')}
              className={`rounded-full p-2 transition-all duration-200 ${
                viewMode === 'card'
                  ? activeViewClasses
                  : 'hover-action text-gray-900 dark:text-gray-100'
              }`}
              title={t('Card view')}
              aria-label={t('Card view')}
              aria-pressed={viewMode === 'card'}
            >
              <svg
                className="h-5 w-5"
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
            type="button"
            onClick={() => updateViewMode('compact')}
            className={`rounded-full p-2 transition-all duration-200 ${
              viewMode === 'compact'
                ? activeViewClasses
                : 'hover-action text-gray-900 dark:text-gray-100'
            }`}
            title={t('Compact view')}
            aria-label={t('Compact view')}
            aria-pressed={viewMode === 'compact'}
          >
            <svg
              className="h-5 w-5"
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
            type="button"
            onClick={() => updateViewMode('list')}
            className={`rounded-full p-2 transition-all duration-200 ${
              viewMode === 'list'
                ? activeViewClasses
                : 'hover-action text-gray-900 dark:text-gray-100'
            }`}
            title={t('List view')}
            aria-label={t('List view')}
            aria-pressed={viewMode === 'list'}
          >
            <svg
              className="h-5 w-5"
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
        <ListView
          books={books}
          onDetails={onDetails}
          onDownload={onDownload}
          onGetReleases={onGetReleases}
          getButtonState={getButtonState}
          getUniversalButtonState={getUniversalButtonState}
          showSeriesPosition={sortValue === 'series_order'}
          onShowToast={onShowToast}
        />
      ) : (
        <div
          id="results-grid"
          className={`grid gap-8 ${!isDesktop ? GRID_CLASSES.mobile : GRID_CLASSES[viewMode]}`}
        >
          {books.map((book, index) => {
            const shouldUseCardLayout = isDesktop && viewMode === 'card';
            const animationDelay = index * 50;
            // Use appropriate button state function based on search mode
            const buttonState =
              searchMode === 'universal'
                ? getUniversalButtonState(book.id)
                : getButtonState(book.id);

            return shouldUseCardLayout ? (
              <CardView
                key={book.id}
                book={book}
                onDetails={onDetails}
                onDownload={onDownload}
                onGetReleases={onGetReleases}
                buttonState={buttonState}
                animationDelay={animationDelay}
                showSeriesPosition={sortValue === 'series_order'}
                onShowToast={onShowToast}
              />
            ) : (
              <CompactView
                key={book.id}
                book={book}
                onDetails={onDetails}
                onDownload={onDownload}
                onGetReleases={onGetReleases}
                buttonState={buttonState}
                showDetailsButton={!isDesktop}
                animationDelay={animationDelay}
                showSeriesPosition={sortValue === 'series_order'}
                onShowToast={onShowToast}
              />
            );
          })}
        </div>
      )}
      {books.length === 0 && (
        <div className="mt-4 text-sm opacity-80">{t('No results found.')}</div>
      )}

      {/* Load More button (universal mode pagination) */}
      {searchMode === 'universal' && hasMore && onLoadMore && (
        <div className="mt-12 flex flex-col items-center gap-2">
          <button
            type="button"
            onClick={onLoadMore}
            disabled={isLoadingMore}
            className={`rounded-full px-3 py-2 text-sm font-medium transition-all duration-200 ${
              isLoadingMore
                ? 'cursor-wait bg-gray-300 text-gray-500 dark:bg-gray-600 dark:text-gray-400'
                : 'bg-emerald-600 text-white hover:bg-emerald-700'
            }`}
          >
            {isLoadingMore ? (
              <span className="flex items-center gap-2">
                <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24">
                  <circle
                    className="opacity-25"
                    cx="12"
                    cy="12"
                    r="10"
                    stroke="currentColor"
                    strokeWidth="4"
                    fill="none"
                  />
                  <path
                    className="opacity-75"
                    fill="currentColor"
                    d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                  />
                </svg>
                {t('Loading...')}
              </span>
            ) : (
              t('Load More')
            )}
          </button>
          {totalFound !== undefined && totalFound > 0 && (
            <span className="text-sm text-gray-500 dark:text-gray-400">
              {t('Showing {shown} of {total} results', { shown: books.length, total: totalFound })}
            </span>
          )}
        </div>
      )}
    </section>
  );
};

interface SortControlProps {
  value: string;
  onChange: (value: string) => void;
  metadataSortOptions?: SortOption[];
}

// Default universal mode sort options (fallback if not provided by API)
const DEFAULT_UNIVERSAL_SORT_OPTIONS: SortOption[] = [
  { value: 'relevance', label: 'Most relevant' },
];

const SortControl = ({ value, onChange, metadataSortOptions }: SortControlProps) => {
  const { searchMode } = useSearchMode();
  // Use different sort options based on search mode
  // For universal mode, use dynamic options from API (with fallback)
  let sortOptions = SORT_OPTIONS;
  if (searchMode === 'universal') {
    sortOptions =
      metadataSortOptions && metadataSortOptions.length > 0
        ? metadataSortOptions
        : DEFAULT_UNIVERSAL_SORT_OPTIONS;
  }
  const selected = sortOptions.find((option) => option.value === value) ?? sortOptions[0];

  return (
    <Dropdown
      align="left"
      widthClassName="w-60 sm:w-72"
      renderTrigger={({ isOpen, toggle }) => (
        <button
          type="button"
          onClick={toggle}
          className={`hover-action relative flex items-center gap-2 rounded-full px-3 py-2 text-gray-900 transition-all duration-200 dark:text-gray-100 ${
            isOpen ? 'bg-(--hover-action)' : ''
          } animate-pop-up`}
          aria-haspopup="listbox"
          aria-expanded={isOpen}
          aria-label={t('Change sort order')}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.5}
            stroke="currentColor"
            className="h-5 w-5 sm:h-6 sm:w-6"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M3 7.5 7.5 3m0 0L12 7.5M7.5 3v13.5m13.5 0L16.5 21m0 0L12 16.5m4.5 4.5V7.5"
            />
          </svg>
          <span className="text-sm font-medium whitespace-nowrap">{t(selected.label)}</span>
        </button>
      )}
    >
      {({ close }) => (
        <div role="listbox" aria-label={t('Sort results')}>
          {sortOptions.map((option) => {
            const isSelected = option.value === selected.value;
            let selectedClassName = '';
            if (isSelected) {
              selectedClassName =
                searchMode === 'universal'
                  ? 'font-medium text-emerald-600 dark:text-emerald-400'
                  : 'font-medium text-sky-600 dark:text-sky-300';
            }
            return (
              <button
                type="button"
                key={option.value || 'default'}
                className={`hover-surface flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-base ${selectedClassName}`}
                onClick={() => {
                  onChange(option.value);
                  close();
                }}
                role="option"
                aria-selected={isSelected}
              >
                <span>{t(option.label)}</span>
                {isSelected && (
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    fill="none"
                    viewBox="0 0 24 24"
                    strokeWidth={1.5}
                    stroke="currentColor"
                    className="h-4 w-4"
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

import { KeyboardEvent, InputHTMLAttributes, useRef, forwardRef, useImperativeHandle, useState, useEffect } from 'react';
import { useSearchMode } from '../contexts/SearchModeContext';
import { ContentType } from '../types';

interface SearchBarProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  isLoading?: boolean;
  onAdvancedToggle?: () => void;
  placeholder?: string;
  inputAriaLabel?: string;
  className?: string;
  inputClassName?: string;
  controlsClassName?: string;
  clearButtonLabel?: string;
  clearButtonTitle?: string;
  advancedButtonLabel?: string;
  advancedButtonTitle?: string;
  searchButtonLabel?: string;
  searchButtonTitle?: string;
  autoComplete?: string;
  enterKeyHint?: InputHTMLAttributes<HTMLInputElement>['enterKeyHint'];
  // Content type selector props
  contentType?: ContentType;
  onContentTypeChange?: (type: ContentType) => void;
}

export interface SearchBarHandle {
  submit: () => void;
}

export const SearchBar = forwardRef<SearchBarHandle, SearchBarProps>(({
  value,
  onChange,
  onSubmit,
  isLoading = false,
  onAdvancedToggle,
  placeholder = 'Search by ISBN, title, author...',
  inputAriaLabel = 'Search books',
  className = '',
  inputClassName = '',
  controlsClassName = '',
  clearButtonLabel = 'Clear search input',
  clearButtonTitle = 'Clear search',
  advancedButtonLabel = 'Advanced Search',
  advancedButtonTitle = 'Advanced Search',
  searchButtonLabel = 'Search books',
  searchButtonTitle = 'Search',
  autoComplete = 'off',
  enterKeyHint = 'search',
  contentType = 'ebook',
  onContentTypeChange,
}, ref) => {
  const { searchMode, isUniversalMode } = useSearchMode();
  const inputRef = useRef<HTMLInputElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const hasSearchQuery = value.trim().length > 0;

  // Content type dropdown state
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const showContentTypeSelector = isUniversalMode && !!onContentTypeChange;

  // Dynamic placeholder based on content type
  const effectivePlaceholder = showContentTypeSelector
    ? (contentType === 'ebook' ? 'Search books by title, author, ISBN...' : 'Search audiobooks by title, author...')
    : placeholder;

  // Close dropdown on click outside or escape
  useEffect(() => {
    if (!isDropdownOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false);
      }
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsDropdownOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape as unknown as EventListener);

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape as unknown as EventListener);
    };
  }, [isDropdownOpen]);

  const handleContentTypeSelect = (type: ContentType) => {
    onContentTypeChange?.(type);
    setIsDropdownOpen(false);
  };

  useImperativeHandle(ref, () => ({
    submit: () => {
      buttonRef.current?.click();
    },
  }));

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      onSubmit();
      (e.target as HTMLInputElement).blur();
    }
  };

  const handleClearSearch = () => {
    onChange('');
    inputRef.current?.focus();
  };

  const wrapperClasses = ['relative', className].filter(Boolean).join(' ').trim();
  const inputClasses = [
    'w-full pr-40 py-3 border outline-none search-input',
    showContentTypeSelector ? 'pl-3 rounded-r-full' : 'pl-4 rounded-full',
    inputClassName,
  ]
    .filter(Boolean)
    .join(' ')
    .trim();
  const controlsClasses = [
    'absolute inset-y-0 right-0 flex items-center gap-1 pr-2',
    controlsClassName,
  ]
    .filter(Boolean)
    .join(' ')
    .trim();

  // Content type icons
  const BookIcon = () => (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25" />
    </svg>
  );

  const AudiobookIcon = () => (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z" />
    </svg>
  );

  return (
    <div className={wrapperClasses}>
      <div
        className="flex items-stretch rounded-full border"
        style={{
          background: 'var(--bg-soft)',
          borderColor: 'var(--border-muted)',
        }}
      >
        {/* Content Type Selector */}
        {showContentTypeSelector && (
          <div className="relative flex-shrink-0 flex" ref={dropdownRef}>
            <button
              type="button"
              onClick={() => setIsDropdownOpen(!isDropdownOpen)}
              className="flex items-center gap-1.5 pl-5 pr-2 rounded-l-full transition-colors hover-action"
              style={{ color: 'var(--text)' }}
              aria-label={`Searching ${contentType === 'ebook' ? 'books' : 'audiobooks'}. Click to change.`}
              aria-expanded={isDropdownOpen}
              aria-haspopup="listbox"
            >
              {contentType === 'ebook' ? <BookIcon /> : <AudiobookIcon />}
              <svg
                className={`w-3 h-3 opacity-50 transition-transform duration-200 ${isDropdownOpen ? 'rotate-180' : ''}`}
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                strokeWidth="2.5"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
              </svg>
            </button>

            {/* Divider */}
            <div
              className="absolute right-0 top-1/2 -translate-y-1/2 w-px h-6"
              style={{ background: 'var(--border-muted)' }}
            />

            {/* Dropdown Menu */}
            {isDropdownOpen && (
              <div
                className="absolute left-0 top-full mt-2 w-40 rounded-lg border shadow-lg z-50 overflow-hidden animate-fade-in-down"
                style={{
                  background: 'var(--bg)',
                  borderColor: 'var(--border-muted)',
                }}
                role="listbox"
                aria-label="Content type options"
              >
                <button
                  type="button"
                  onClick={() => handleContentTypeSelect('ebook')}
                  className={`w-full px-3 py-2.5 text-sm font-medium flex items-center gap-2.5 transition-colors ${
                    contentType === 'ebook'
                      ? 'bg-emerald-600 text-white'
                      : 'hover-surface'
                  }`}
                  style={contentType !== 'ebook' ? { color: 'var(--text)' } : undefined}
                  role="option"
                  aria-selected={contentType === 'ebook'}
                >
                  <BookIcon />
                  Books
                  {contentType === 'ebook' && (
                    <svg className="w-4 h-4 ml-auto" fill="none" viewBox="0 0 24 24" strokeWidth="2" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                    </svg>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => handleContentTypeSelect('audiobook')}
                  className={`w-full px-3 py-2.5 text-sm font-medium flex items-center gap-2.5 transition-colors border-t ${
                    contentType === 'audiobook'
                      ? 'bg-emerald-600 text-white'
                      : 'hover-surface'
                  }`}
                  style={{
                    borderColor: 'var(--border-muted)',
                    ...(contentType !== 'audiobook' ? { color: 'var(--text)' } : {}),
                  }}
                  role="option"
                  aria-selected={contentType === 'audiobook'}
                >
                  <AudiobookIcon />
                  Audiobooks
                  {contentType === 'audiobook' && (
                    <svg className="w-4 h-4 ml-auto" fill="none" viewBox="0 0 24 24" strokeWidth="2" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                    </svg>
                  )}
                </button>
              </div>
            )}
          </div>
        )}

        {/* Search Input */}
        <input
          type="search"
          placeholder={effectivePlaceholder}
          aria-label={inputAriaLabel}
          autoComplete={autoComplete}
          enterKeyHint={enterKeyHint}
          className={inputClasses}
          style={{
            background: 'transparent',
            color: 'var(--text)',
            border: 'none',
          }}
          value={value}
          onChange={e => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          ref={inputRef}
        />
      </div>

      {/* Right-side controls */}
      <div className={controlsClasses}>
        {hasSearchQuery && (
          <button
            type="button"
            onClick={handleClearSearch}
            className="p-2 rounded-full hover-action flex items-center justify-center transition-colors"
            aria-label={clearButtonLabel}
            title={clearButtonTitle}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
              stroke="currentColor"
              className="w-5 h-5"
              style={{ color: 'var(--text)' }}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        )}
        {onAdvancedToggle && (
          <button
            type="button"
            onClick={onAdvancedToggle}
            className="p-2 rounded-full hover-action flex items-center justify-center transition-colors"
            aria-label={advancedButtonLabel}
            title={advancedButtonTitle}
          >
            <svg
              className="w-5 h-5"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
              stroke="currentColor"
              style={{ color: 'var(--text)' }}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M10.5 6h9.75M10.5 6a1.5 1.5 0 1 1-3 0m3 0a1.5 1.5 0 1 0-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-9.75 0h9.75"
              />
            </svg>
          </button>
        )}
        <button
          ref={buttonRef}
          type="button"
          onClick={onSubmit}
          className={`p-2 rounded-full text-white disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center transition-colors search-bar-button ${
            searchMode === 'universal'
              ? 'bg-emerald-600 hover:bg-emerald-700'
              : 'bg-sky-700 hover:bg-sky-800'
          }`}
          aria-label={searchButtonLabel}
          title={searchButtonTitle}
          disabled={isLoading}
        >
          {!isLoading && (
            <svg
              className="w-5 h-5 search-bar-icon"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="2"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
              />
            </svg>
          )}
          {isLoading && (
            <div className="spinner w-3 h-3 border-2 border-white border-t-transparent search-bar-spinner" />
          )}
        </button>
      </div>
    </div>
  );
});

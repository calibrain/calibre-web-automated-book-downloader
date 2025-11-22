import { KeyboardEvent, InputHTMLAttributes, useRef } from 'react';

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
}

export const SearchBar = ({
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
}: SearchBarProps) => {
  const inputRef = useRef<HTMLInputElement>(null);
  const hasSearchQuery = value.trim().length > 0;

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
    'w-full pl-4 pr-40 py-3 rounded-full border outline-none search-input',
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

  return (
    <div className={wrapperClasses}>
      <input
        type="search"
        placeholder={placeholder}
        aria-label={inputAriaLabel}
        autoComplete={autoComplete}
        enterKeyHint={enterKeyHint}
        className={inputClasses}
        style={{
          background: 'var(--bg-soft)',
          color: 'var(--text)',
          borderColor: 'var(--border-muted)',
        }}
        value={value}
        onChange={e => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        ref={inputRef}
      />
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
          type="button"
          onClick={onSubmit}
          className="p-2 rounded-full text-white bg-sky-700 hover:bg-sky-800 disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center transition-colors search-bar-button"
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
};



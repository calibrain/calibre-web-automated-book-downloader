import { CSSProperties } from 'react';
import { Book, ButtonStateInfo } from '../types';
import { CircularProgress } from './shared';

type ButtonSize = 'sm' | 'md';
type ButtonVariant = 'default' | 'icon';

interface BookGetButtonProps {
  book: Book;
  onGetReleases: (book: Book) => void;
  buttonState?: ButtonStateInfo;
  isLoading?: boolean;
  size?: ButtonSize;
  variant?: ButtonVariant;
  fullWidth?: boolean;
  className?: string;
  style?: CSSProperties;
}

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'px-2.5 py-1.5 text-xs',
  md: 'px-4 py-2.5 text-sm',
};

const iconSizeClasses: Record<ButtonSize, string> = {
  sm: 'p-1.5',
  md: 'p-1.5 sm:p-2',
};

const iconSizes: Record<ButtonSize, string> = {
  sm: 'w-3.5 h-3.5',
  md: 'w-4 h-4',
};

const iconOnlySizes: Record<ButtonSize, string> = {
  sm: 'w-4 h-4',
  md: 'w-4 h-4 sm:w-5 sm:h-5',
};

export const BookGetButton = ({
  book,
  onGetReleases,
  buttonState,
  isLoading = false,
  size = 'md',
  variant = 'default',
  fullWidth = false,
  className = '',
  style,
}: BookGetButtonProps) => {
  const isIconVariant = variant === 'icon';
  const widthClasses = fullWidth ? 'w-full' : '';
  const sizeClass = isIconVariant ? iconSizeClasses[size] : sizeClasses[size];
  const iconSize = isIconVariant ? iconOnlySizes[size] : iconSizes[size];

  // Determine states based on buttonState
  const isCompleted = buttonState?.state === 'complete';
  const hasError = buttonState?.state === 'error';
  const isInProgress = buttonState && ['queued', 'resolving', 'downloading'].includes(buttonState.state);
  const showCircularProgress = buttonState?.state === 'downloading' && buttonState.progress !== undefined;
  const showSpinner = (isInProgress && !showCircularProgress) || isLoading;

  // Disable button while loading metadata
  const isDisabled = isLoading;

  // Determine button styling based on state
  const getButtonClasses = () => {
    if (isCompleted) {
      return isIconVariant
        ? 'bg-green-600 text-white'
        : 'bg-green-600 hover:bg-green-700';
    }
    if (hasError) {
      return isIconVariant
        ? 'bg-red-600 text-white opacity-75'
        : 'bg-red-600 hover:bg-red-700';
    }
    if (isLoading) {
      // Show loading state (fetching metadata)
      return isIconVariant
        ? 'text-gray-400 dark:text-gray-500'
        : 'bg-emerald-600/70';
    }
    if (isInProgress) {
      // Show progress state but keep it clickable
      return isIconVariant
        ? 'bg-sky-600 text-white'
        : 'bg-sky-600 hover:bg-sky-700';
    }
    // Default state - icon variant has no background
    return isIconVariant
      ? 'text-gray-600 dark:text-gray-200 hover-action'
      : 'bg-emerald-600 hover:bg-emerald-700';
  };

  const handleClick = () => {
    if (isDisabled) return;
    onGetReleases(book);
  };

  // Determine display text
  const getDisplayText = () => {
    if (isCompleted) return 'Downloaded';
    if (hasError) return 'Failed';
    if (isLoading) return 'Loading';
    if (buttonState?.state === 'downloading') return 'Downloading';
    if (buttonState?.state === 'resolving') return 'Resolving';
    if (buttonState?.state === 'queued') return 'Queued';
    return 'Get';
  };

  // Render appropriate icon based on state
  const renderIcon = () => {
    if (isCompleted) {
      return (
        <svg className={iconSize} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      );
    }

    if (hasError) {
      return (
        <svg className={iconSize} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      );
    }

    if (showCircularProgress) {
      const progressSize = isIconVariant ? (size === 'sm' ? 16 : 20) : (size === 'sm' ? 12 : 16);
      return <CircularProgress progress={buttonState?.progress} size={progressSize} />;
    }

    if (showSpinner) {
      return (
        <div
          className={`${iconSize} border-2 border-current border-t-transparent rounded-full animate-spin`}
        />
      );
    }

    // Default "+" icon for Get action
    return (
      <svg className={iconSize} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
      </svg>
    );
  };

  // Icon variant renders as a circular button without text
  if (isIconVariant) {
    return (
      <button
        className={`flex items-center justify-center rounded-full transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-emerald-500 ${sizeClass} ${getButtonClasses()} ${className}`.trim()}
        onClick={handleClick}
        disabled={isDisabled}
        style={style}
        aria-label={`${getDisplayText()} releases for ${book.title}`}
      >
        {renderIcon()}
      </button>
    );
  }

  return (
    <button
      className={`inline-flex items-center justify-center gap-1.5 rounded text-white transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-emerald-500 ${sizeClass} ${widthClasses} ${getButtonClasses()} ${className}`.trim()}
      onClick={handleClick}
      disabled={isDisabled}
      style={style}
      aria-label={`${getDisplayText()} releases for ${book.title}`}
    >
      {renderIcon()}
      <span>{getDisplayText()}</span>
    </button>
  );
};

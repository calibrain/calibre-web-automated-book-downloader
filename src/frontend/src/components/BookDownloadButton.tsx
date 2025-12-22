import { useEffect, useState, CSSProperties } from 'react';
import { ButtonStateInfo } from '../types';
import { CircularProgress } from './shared';

type ButtonSize = 'sm' | 'md';
type ButtonVariant = 'primary' | 'icon';

interface BookDownloadButtonProps {
  buttonState: ButtonStateInfo;
  onDownload: () => Promise<void>;
  size?: ButtonSize;
  fullWidth?: boolean;
  className?: string;
  showIcon?: boolean;
  style?: CSSProperties;
  variant?: ButtonVariant;
  ariaLabel?: string;
}

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'px-2.5 py-1.5 text-xs',
  md: 'px-4 py-2.5 text-sm',
};

const iconVariantSizeClasses: Record<ButtonSize, string> = {
  sm: 'p-px m-0.5 sm:p-1 sm:m-0.5 aspect-square',
  md: 'p-0.5 m-0.5 sm:p-1.5 sm:m-0.5 aspect-square',
};

const primaryIconSizes: Record<ButtonSize, string> = {
  sm: 'w-3.5 h-3.5',
  md: 'w-4 h-4',
};

const iconVariantIconSizes: Record<ButtonSize, { mobile: string; desktop: string }> = {
  sm: { mobile: 'w-5 h-5', desktop: 'w-5 h-5' },
  md: { mobile: 'w-6 h-6', desktop: 'w-6 h-6' },
};

const iconVariantProgressSizes: Record<ButtonSize, { mobile: number; desktop: number }> = {
  sm: { mobile: 20, desktop: 20 },
  md: { mobile: 24, desktop: 24 },
};

export const BookDownloadButton = ({
  buttonState,
  onDownload,
  size = 'md',
  fullWidth = false,
  className = '',
  showIcon = false,
  style,
  variant = 'primary',
  ariaLabel,
}: BookDownloadButtonProps) => {
  const [isQueuing, setIsQueuing] = useState(false);

  useEffect(() => {
    if (isQueuing && buttonState.state !== 'download') {
      setIsQueuing(false);
    }
  }, [buttonState.state, isQueuing]);

  const isCompleted = buttonState.state === 'complete';
  const hasError = buttonState.state === 'error';
  const isInProgress = ['queued', 'resolving', 'downloading'].includes(buttonState.state);
  const isDisabled = buttonState.state !== 'download' || isQueuing || isCompleted;
  const displayText = isQueuing ? 'Queuing...' : buttonState.text;
  const showCircularProgress = buttonState.state === 'downloading' && buttonState.progress !== undefined;
  const showSpinner = (isInProgress && !showCircularProgress) || isQueuing;

  const primaryStateClasses =
    isCompleted
      ? 'bg-green-600 cursor-not-allowed'
      : hasError
      ? 'bg-red-600 cursor-not-allowed opacity-75'
      : isInProgress
      ? 'bg-gray-500 cursor-not-allowed opacity-75'
      : 'bg-sky-700 hover:bg-sky-800';

  const iconStateClasses =
    isCompleted
      ? 'bg-green-600 text-white cursor-not-allowed'
      : hasError
      ? 'bg-red-600 text-white cursor-not-allowed opacity-75'
      : isInProgress
      ? 'bg-gray-500 text-white cursor-not-allowed opacity-75'
      : 'text-gray-600 dark:text-gray-200 hover-action';

  const stateClasses = variant === 'icon' ? iconStateClasses : primaryStateClasses;
  const widthClasses = variant === 'primary' && fullWidth ? 'w-full' : '';

  const baseClasses =
    variant === 'icon'
      ? 'flex items-center justify-center rounded-full transition-all duration-200 disabled:opacity-80 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-sky-500'
      : 'inline-flex items-center justify-center gap-1.5 rounded text-white transition-all duration-200 disabled:opacity-80 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-sky-500';

  const sizeClass = variant === 'icon' ? iconVariantSizeClasses[size] : sizeClasses[size];
  const iconSizes = variant === 'icon' ? iconVariantIconSizes[size] : undefined;

  const handleDownload = async () => {
    if (isDisabled) return;
    setIsQueuing(true);
    try {
      await onDownload();
    } catch (error) {
      setIsQueuing(false);
    }
  };

  const renderStatusIcon = () => {
    if (isCompleted) {
      if (variant === 'icon' && iconSizes) {
        return (
          <>
            <svg className={`${iconSizes.mobile} sm:hidden`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M5 13l4 4L19 7" />
            </svg>
            <svg className={`${iconSizes.desktop} hidden sm:block`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M5 13l4 4L19 7" />
            </svg>
          </>
        );
      }
      return (
        <svg className={primaryIconSizes[size]} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      );
    }

    if (hasError) {
      if (variant === 'icon' && iconSizes) {
        return (
          <>
            <svg className={`${iconSizes.mobile} sm:hidden`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M6 18L18 6M6 6l12 12" />
            </svg>
            <svg className={`${iconSizes.desktop} hidden sm:block`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </>
        );
      }
      return (
        <svg className={primaryIconSizes[size]} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      );
    }

    if (showCircularProgress) {
      if (variant === 'icon') {
        const progressSize = iconVariantProgressSizes[size].mobile;
        return <CircularProgress progress={buttonState.progress} size={progressSize} />;
      }
      return <CircularProgress progress={buttonState.progress} size={size === 'sm' ? 12 : 16} />;
    }

    if (showSpinner) {
      if (variant === 'icon' && iconSizes) {
        return (
          <div className={`${iconSizes.mobile} border-2 border-current border-t-transparent rounded-full animate-spin`} />
        );
      }
      const spinnerClass = size === 'sm' ? 'w-3 h-3' : 'w-4 h-4';
      return <div className={`${spinnerClass} border-2 border-current border-t-transparent rounded-full animate-spin`} />;
    }

    if (variant === 'icon' && iconSizes) {
      return (
        <>
          <svg className={`${iconSizes.mobile} sm:hidden`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
          </svg>
          <svg className={`${iconSizes.desktop} hidden sm:block`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
          </svg>
        </>
      );
    }

    return null;
  };

  return (
    <button
      className={`${baseClasses} ${sizeClass} ${stateClasses} ${widthClasses} ${className}`.trim()}
      onClick={handleDownload}
      disabled={isDisabled || isInProgress}
      data-action="download"
      style={style}
      aria-label={ariaLabel ?? displayText}
    >
      {variant === 'primary' && showIcon && !isCompleted && !hasError && !showCircularProgress && !showSpinner && (
        <svg className={primaryIconSizes[size]} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v12m0 0l-4-4m4 4 4-4M6 20h12" />
        </svg>
      )}

      {variant === 'primary' && <span className="download-button-text">{displayText}</span>}
      {variant === 'icon' && <span className="sr-only">{ariaLabel ?? displayText}</span>}

      {renderStatusIcon()}
    </button>
  );
};


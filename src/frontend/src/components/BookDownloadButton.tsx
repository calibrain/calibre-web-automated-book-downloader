import { useEffect, useState, CSSProperties } from 'react';
import { ButtonStateInfo } from '../types';
import { CircularProgress } from './CircularProgress';

type ButtonSize = 'sm' | 'md';

interface BookDownloadButtonProps {
  buttonState: ButtonStateInfo;
  onDownload: () => Promise<void>;
  size?: ButtonSize;
  fullWidth?: boolean;
  className?: string;
  showIcon?: boolean;
  style?: CSSProperties;
}

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'px-2.5 py-1.5 text-xs',
  md: 'px-4 py-2.5 text-sm',
};

const iconSizes: Record<ButtonSize, string> = {
  sm: 'w-3.5 h-3.5',
  md: 'w-4 h-4',
};

export const BookDownloadButton = ({
  buttonState,
  onDownload,
  size = 'md',
  fullWidth = false,
  className = '',
  showIcon = false,
  style,
}: BookDownloadButtonProps) => {
  const [isQueuing, setIsQueuing] = useState(false);

  useEffect(() => {
    if (isQueuing && buttonState.state !== 'download') {
      setIsQueuing(false);
    }
  }, [buttonState.state, isQueuing]);

  const isCompleted = buttonState.state === 'completed';
  const hasError = buttonState.state === 'error';
  const isInProgress = ['queued', 'resolving', 'bypassing', 'downloading', 'verifying', 'ingesting'].includes(
    buttonState.state,
  );
  const isDisabled = buttonState.state !== 'download' || isQueuing || isCompleted;
  const displayText = isQueuing ? 'Queuing...' : buttonState.text;
  const showCircularProgress = buttonState.state === 'downloading' && buttonState.progress !== undefined;
  const showSpinner = (isInProgress && !showCircularProgress) || isQueuing;

  const stateClasses =
    isCompleted
      ? 'bg-green-600 cursor-not-allowed'
      : hasError
      ? 'bg-red-600 cursor-not-allowed opacity-75'
      : isInProgress
      ? 'bg-gray-500 cursor-not-allowed opacity-75'
      : 'bg-sky-700 hover:bg-sky-800';

  const widthClasses = fullWidth ? 'w-full' : '';
  const baseClasses =
    'inline-flex items-center justify-center gap-1.5 rounded text-white transition-all duration-200 disabled:opacity-80 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-sky-500';

  const handleDownload = async () => {
    if (isDisabled) return;
    setIsQueuing(true);
    try {
      await onDownload();
    } catch (error) {
      setIsQueuing(false);
    }
  };

  return (
    <button
      className={`${baseClasses} ${sizeClasses[size]} ${stateClasses} ${widthClasses} ${className}`.trim()}
      onClick={handleDownload}
      disabled={isDisabled || isInProgress}
      data-action="download"
      style={style}
    >
      {showIcon && !isCompleted && !hasError && !showCircularProgress && !showSpinner && (
        <svg className={iconSizes[size]} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v12m0 0l-4-4m4 4 4-4M6 20h12" />
        </svg>
      )}
      <span className="download-button-text">{displayText}</span>
      {isCompleted && (
        <svg className={iconSizes[size]} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      )}
      {hasError && (
        <svg className={iconSizes[size]} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      )}
      {showCircularProgress && <CircularProgress progress={buttonState.progress} size={size === 'sm' ? 12 : 16} />}
      {showSpinner && (
        <div
          className={`${size === 'sm' ? 'w-3 h-3' : 'w-4 h-4'} border-2 border-white border-t-transparent rounded-full animate-spin`}
        />
      )}
    </button>
  );
};


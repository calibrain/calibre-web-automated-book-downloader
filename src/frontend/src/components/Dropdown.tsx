import { ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

// Simple throttle function to limit how often a function can be called
function throttle<T extends (...args: unknown[]) => void>(fn: T, delay: number): T {
  let lastCall = 0;
  let timeoutId: ReturnType<typeof setTimeout> | null = null;

  return ((...args: unknown[]) => {
    const now = Date.now();
    const timeSinceLastCall = now - lastCall;

    if (timeSinceLastCall >= delay) {
      lastCall = now;
      fn(...args);
    } else if (!timeoutId) {
      // Schedule a trailing call
      timeoutId = setTimeout(() => {
        lastCall = Date.now();
        timeoutId = null;
        fn(...args);
      }, delay - timeSinceLastCall);
    }
  }) as T;
}

interface DropdownProps {
  label?: string;
  summary?: ReactNode;
  children: (helpers: { close: () => void }) => ReactNode;
  align?: 'left' | 'right';
  widthClassName?: string;
  buttonClassName?: string;
  panelClassName?: string;
  disabled?: boolean;
  renderTrigger?: (props: { isOpen: boolean; toggle: () => void }) => ReactNode;
  /** Disable max-height and overflow scrolling (for panels with nested dropdowns) */
  noScrollLimit?: boolean;
}

export const Dropdown = ({
  label,
  summary,
  children,
  align = 'left',
  widthClassName = 'w-full',
  buttonClassName = '',
  panelClassName = '',
  disabled = false,
  renderTrigger,
  noScrollLimit = false,
}: DropdownProps) => {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [panelDirection, setPanelDirection] = useState<'down' | 'up'>('down');

  const toggleOpen = () => {
    if (disabled) return;
    setIsOpen(prev => !prev);
  };

  const close = () => setIsOpen(false);

  useEffect(() => {
    if (!isOpen) return;

    const handleClick = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        close();
      }
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        close();
      }
    };

    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleEscape);

    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen]);

  // Memoize the panel direction calculation
  const updatePanelDirection = useCallback(() => {
    if (!containerRef.current || !panelRef.current) {
      return;
    }

    const rect = containerRef.current.getBoundingClientRect();
    const panelHeight = panelRef.current.offsetHeight || panelRef.current.scrollHeight;
    const spaceBelow = window.innerHeight - rect.bottom - 8;
    const spaceAbove = rect.top - 8;
    const shouldOpenUp = spaceBelow < panelHeight && spaceAbove >= panelHeight;

    setPanelDirection(shouldOpenUp ? 'up' : 'down');
  }, []);

  useLayoutEffect(() => {
    if (!isOpen) return;

    // Throttle scroll/resize handlers to reduce layout thrashing
    const throttledUpdate = throttle(updatePanelDirection, 100);

    updatePanelDirection();
    window.addEventListener('resize', throttledUpdate);
    window.addEventListener('scroll', throttledUpdate, true);

    return () => {
      window.removeEventListener('resize', throttledUpdate);
      window.removeEventListener('scroll', throttledUpdate, true);
    };
  }, [isOpen, updatePanelDirection]);

  return (
    <div className={`relative ${widthClassName}`} ref={containerRef}>
      {label && (
        <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5" onClick={toggleOpen}>
          {label}
        </label>
      )}
      {renderTrigger ? (
        renderTrigger({ isOpen, toggle: toggleOpen })
      ) : (
        <button
          type="button"
          onClick={toggleOpen}
          disabled={disabled}
          className={`w-full px-3 py-2 text-sm rounded-lg border flex items-center justify-between text-left focus:outline-none focus-visible:outline-none focus-visible:ring-0 focus-visible:ring-offset-0 ${buttonClassName}`}
          style={{
            background: 'var(--bg-soft)',
            color: 'var(--text)',
            borderColor: 'var(--border-muted)',
          }}
        >
          <span className="truncate">
            {summary ?? <span className="opacity-60">Select an option</span>}
          </span>
          <svg
            className={`w-4 h-4 transition-transform ${isOpen ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            strokeWidth="1.5"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
          </svg>
        </button>
      )}

      {isOpen && (
        <div
          ref={panelRef}
          className={`absolute ${align === 'right' ? 'right-0' : 'left-0'} ${
            panelDirection === 'down' ? 'mt-2' : 'bottom-full mb-2'
          } rounded-lg border shadow-lg z-20 ${panelClassName || widthClassName}`}
          style={{
            background: 'var(--bg)',
            borderColor: 'var(--border-muted)',
          }}
        >
          <div className={noScrollLimit ? '' : 'max-h-64 overflow-auto'}>
            {children({ close })}
          </div>
        </div>
      )}
    </div>
  );
};


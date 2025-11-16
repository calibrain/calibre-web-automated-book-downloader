import { ReactNode, useEffect, useRef, useState } from 'react';

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
}: DropdownProps) => {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

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

  return (
    <div className={`relative ${widthClassName}`} ref={containerRef}>
      {label && (
        <label className="block text-sm mb-1 opacity-80" onClick={toggleOpen}>
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
          className={`w-full px-3 py-2 rounded-md border flex items-center justify-between text-left text-base focus:outline-none focus-visible:outline-none focus-visible:ring-0 focus-visible:ring-offset-0 ${buttonClassName}`}
          style={{
            background: 'var(--bg-soft)',
            color: 'var(--text)',
            borderColor: 'var(--border-muted)',
          }}
        >
          <span className="truncate text-base">
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
          className={`absolute ${align === 'right' ? 'right-0' : 'left-0'} mt-2 rounded-md border shadow-lg z-20 ${panelClassName || widthClassName}`}
          style={{
            background: 'var(--bg)',
            borderColor: 'var(--border-muted)',
          }}
        >
          <div className="max-h-64 overflow-auto">
            {children({ close })}
          </div>
        </div>
      )}
    </div>
  );
};


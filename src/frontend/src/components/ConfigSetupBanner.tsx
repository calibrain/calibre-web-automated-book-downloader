import { useState, useEffect, useCallback } from 'react';

const STORAGE_KEY = 'cwa-config-banner-dismissed';

interface ConfigSetupBannerProps {
  /** Whether to show the banner (controlled mode) */
  isOpen?: boolean;
  /** Called when banner is closed */
  onClose?: () => void;
  /** Called when "Continue to Settings" is clicked (only shown if provided) */
  onContinue?: () => void;
  /** Auto-show mode: show banner if settings not enabled and not dismissed */
  settingsEnabled?: boolean;
}

export const ConfigSetupBanner = ({
  isOpen: controlledOpen,
  onClose,
  onContinue,
  settingsEnabled,
}: ConfigSetupBannerProps) => {
  const [autoShowVisible, setAutoShowVisible] = useState(false);
  const [isClosing, setIsClosing] = useState(false);

  // Auto-show mode: check localStorage on mount
  useEffect(() => {
    if (settingsEnabled !== undefined) {
      const dismissed = localStorage.getItem(STORAGE_KEY);
      setAutoShowVisible(!settingsEnabled && dismissed !== 'true');
    }
  }, [settingsEnabled]);

  // Determine if we should show based on controlled or auto-show mode
  const isControlledMode = controlledOpen !== undefined;
  const isVisible = isControlledMode ? controlledOpen : autoShowVisible;

  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      setIsClosing(false);
      if (isControlledMode) {
        onClose?.();
      } else {
        // Auto-show mode: save to localStorage
        localStorage.setItem(STORAGE_KEY, 'true');
        setAutoShowVisible(false);
      }
    }, 150);
  }, [isControlledMode, onClose]);

  const handleContinue = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      setIsClosing(false);
      onContinue?.();
    }, 150);
  }, [onContinue]);

  if (!isVisible && !isClosing) return null;

  // Determine which mode we're in for the footer buttons
  const showContinueButton = !!onContinue;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className={`absolute inset-0 bg-black/50 backdrop-blur-sm transition-opacity duration-150
                    ${isClosing ? 'opacity-0' : 'opacity-100'}`}
        onClick={handleClose}
      />

      {/* Modal */}
      <div
        className={`relative w-full max-w-lg rounded-xl
                    border border-[var(--border-muted)] shadow-2xl
                    overflow-hidden
                    ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        style={{ background: 'var(--bg)' }}
        role="dialog"
        aria-modal="true"
        aria-label="Settings Setup Information"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--border-muted)]">
          <h2 className="text-lg font-semibold">
            {showContinueButton ? 'Config Volume Required' : 'New Feature: Settings Page'}
          </h2>
          <button
            onClick={handleClose}
            className="p-1.5 rounded-lg hover:bg-[var(--hover-surface)] transition-colors"
            aria-label="Close"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={1.5}
              stroke="currentColor"
              className="w-5 h-5"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="px-5 py-4 space-y-4">
          <p className="text-sm opacity-80">
            {showContinueButton
              ? 'To save settings, add a config volume to your Docker Compose file:'
              : 'CWA Book Downloader now has a settings page! To enable it, add a config volume to your Docker Compose file:'}
          </p>

          {/* Code snippet */}
          <div className="rounded-lg overflow-hidden border border-[var(--border-muted)]">
            <div className="px-3 py-1.5 text-xs font-medium opacity-60 border-b border-[var(--border-muted)]"
                 style={{ background: 'var(--bg-soft)' }}>
              docker-compose.yml
            </div>
            <pre
              className="px-3 py-3 text-sm overflow-x-auto"
              style={{ background: 'var(--bg-soft)' }}
            >
              <code>
                <span className="opacity-60">services:</span>{'\n'}
                <span className="opacity-60">{'  '}cwa-book-downloader:</span>{'\n'}
                {'    '}volumes:{'\n'}
                {'      '}- <span className="text-blue-400">/path/to/config</span>:<span className="text-green-400">/config</span>
              </code>
            </pre>
          </div>

          <p className="text-xs opacity-60">
            {showContinueButton
              ? 'Without this volume, settings changes will not persist across container restarts.'
              : 'This allows you to configure settings through the UI and persist them across container restarts.'}
          </p>
        </div>

        {/* Footer */}
        <div className="px-5 py-4 border-t border-[var(--border-muted)] flex justify-end gap-3">
          {showContinueButton ? (
            <>
              <button
                onClick={handleClose}
                className="px-4 py-2 rounded-lg text-sm font-medium
                           bg-[var(--bg-soft)] border border-[var(--border-muted)]
                           hover:bg-[var(--hover-surface)] transition-colors"
              >
                Close
              </button>
              <button
                onClick={handleContinue}
                className="px-4 py-2 rounded-lg text-sm font-medium
                           bg-[var(--primary-color)] text-white
                           hover:bg-[var(--primary-dark)] transition-colors"
              >
                Continue to Settings
              </button>
            </>
          ) : (
            <button
              onClick={handleClose}
              className="px-4 py-2 rounded-lg text-sm font-medium
                         bg-[var(--primary-color)] text-white
                         hover:bg-[var(--primary-dark)] transition-colors"
            >
              Got it
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

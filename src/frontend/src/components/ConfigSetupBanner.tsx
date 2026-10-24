import { useCallback, useState } from 'react';

import { t } from '../i18n';

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
  const [isClosing, setIsClosing] = useState(false);
  const [dismissed, setDismissed] = useState(() => {
    if (typeof window === 'undefined') {
      return false;
    }

    try {
      return window.localStorage.getItem(STORAGE_KEY) === 'true';
    } catch {
      return false;
    }
  });

  // Determine if we should show based on controlled or auto-show mode
  const isControlledMode = controlledOpen !== undefined;
  const isAutoShowVisible = settingsEnabled !== undefined ? !settingsEnabled && !dismissed : false;
  const isVisible = isControlledMode ? controlledOpen : isAutoShowVisible;

  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      setIsClosing(false);
      if (isControlledMode) {
        onClose?.();
      } else {
        try {
          window.localStorage.setItem(STORAGE_KEY, 'true');
        } catch {
          // Best effort only.
        }
        setDismissed(true);
      }
    }, 150);
  }, [isControlledMode, onClose, setDismissed]);

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
      <button
        type="button"
        className={`absolute inset-0 bg-black/60 transition-opacity duration-150 ${isClosing ? 'opacity-0' : 'opacity-100'}`}
        onClick={handleClose}
        aria-label={t('Close settings setup dialog')}
      />

      {/* Modal */}
      <div
        className={`relative z-10 w-full max-w-lg overflow-hidden rounded-xl border border-(--border-muted) shadow-2xl ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        style={{ background: 'var(--bg)' }}
        role="dialog"
        aria-modal="true"
        aria-label={t('Settings Setup Information')}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-(--border-muted) px-5 py-4">
          <h2 className="text-lg font-semibold">
            {showContinueButton ? t('Config Volume Required') : t('New Feature: Settings Page')}
          </h2>
          <button
            type="button"
            onClick={handleClose}
            className="rounded-lg p-1.5 transition-colors hover:bg-(--hover-surface)"
            aria-label={t('Close')}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={1.5}
              stroke="currentColor"
              className="h-5 w-5"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="space-y-4 px-5 py-4">
          <p className="text-sm opacity-80">
            {showContinueButton
              ? t('To save settings, add a config volume to your Docker Compose file:')
              : t(
                  'Shelfmark now has a settings page! To enable it, add a config volume to your Docker Compose file:',
                )}
          </p>

          {/* Code snippet */}
          <div className="overflow-hidden rounded-lg border border-(--border-muted)">
            <div
              className="border-b border-(--border-muted) px-3 py-1.5 text-xs font-medium opacity-60"
              style={{ background: 'var(--bg-soft)' }}
            >
              docker-compose.yml
            </div>
            <pre
              className="overflow-x-auto px-3 py-3 text-sm"
              style={{ background: 'var(--bg-soft)' }}
            >
              <code>
                <span className="opacity-60">services:</span>
                {'\n'}
                <span className="opacity-60">{'  '}shelfmark:</span>
                {'\n'}
                {'    '}volumes:{'\n'}
                {'      '}- <span className="text-blue-400">/path/to/config</span>:
                <span className="text-green-400">/config</span>
              </code>
            </pre>
          </div>

          <p className="text-xs opacity-60">
            {showContinueButton
              ? t(
                  'Without this volume, settings changes will not persist across container restarts.',
                )
              : t(
                  'This allows you to configure settings through the UI and persist them across container restarts.',
                )}
          </p>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-3 border-t border-(--border-muted) px-5 py-4">
          {showContinueButton ? (
            <>
              <button
                type="button"
                onClick={handleClose}
                className="rounded-lg border border-(--border-muted) bg-(--bg-soft) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--hover-surface)"
              >
                {t('Close')}
              </button>
              <button
                type="button"
                onClick={handleContinue}
                className="rounded-lg bg-(--primary-color) px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-(--primary-dark)"
              >
                {t('Continue to Settings')}
              </button>
            </>
          ) : (
            <button
              type="button"
              onClick={handleClose}
              className="rounded-lg bg-(--primary-color) px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-(--primary-dark)"
            >
              {t('Got it')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

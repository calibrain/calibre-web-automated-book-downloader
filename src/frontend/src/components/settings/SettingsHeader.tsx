import { t } from '../../i18n';

interface SettingsHeaderProps {
  title: string;
  showBack?: boolean;
  onBack?: () => void;
  onClose: () => void;
}

export const SettingsHeader = ({
  title,
  showBack = false,
  onBack,
  onClose,
}: SettingsHeaderProps) => (
  <header
    className="flex shrink-0 items-center gap-3 border-b border-(--border-muted) px-5 py-4"
    style={{ paddingTop: 'calc(1rem + env(safe-area-inset-top))' }}
  >
    {showBack && (
      <button
        type="button"
        onClick={onBack}
        className="hover-action -ml-2 rounded-full p-2 transition-colors"
        aria-label={t('Go back')}
      >
        <svg
          className="h-5 w-5"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5L8.25 12l7.5-7.5" />
        </svg>
      </button>
    )}
    <h2 className="flex-1 text-lg font-semibold">{title}</h2>
    <button
      type="button"
      onClick={onClose}
      className="hover-action rounded-full p-2 transition-colors"
      aria-label={t('Close settings')}
    >
      <svg
        className="h-5 w-5"
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
        viewBox="0 0 24 24"
        strokeWidth={1.5}
        stroke="currentColor"
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
      </svg>
    </button>
  </header>
);

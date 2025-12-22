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
    className="flex items-center gap-3 px-5 py-4 border-b border-[var(--border-muted)] flex-shrink-0"
    style={{ paddingTop: 'calc(1rem + env(safe-area-inset-top))' }}
  >
    {showBack && (
      <button
        onClick={onBack}
        className="p-2 -ml-2 rounded-full hover-action transition-colors"
        aria-label="Go back"
      >
        <svg
          className="w-5 h-5"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M15.75 19.5L8.25 12l7.5-7.5"
          />
        </svg>
      </button>
    )}
    <h2 className="text-lg font-semibold flex-1">{title}</h2>
    <button
      onClick={onClose}
      className="p-2 rounded-full hover-action transition-colors"
      aria-label="Close settings"
    >
      <svg
        className="w-5 h-5"
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

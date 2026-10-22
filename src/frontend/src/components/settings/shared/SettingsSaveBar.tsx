import { t } from '../../../i18n';

interface SettingsSaveBarProps {
  onSave: () => void | Promise<void>;
  isSaving: boolean;
}

export const SettingsSaveBar = ({ onSave, isSaving }: SettingsSaveBarProps) => (
  <div
    className="animate-slide-up shrink-0 border-t border-(--border-muted) bg-(--bg) px-6 py-4"
    style={{ paddingBottom: 'calc(1rem + env(safe-area-inset-bottom))' }}
  >
    <button
      type="button"
      onClick={() => {
        void onSave();
      }}
      disabled={isSaving}
      className="w-full rounded-lg bg-sky-600 px-4 py-2.5 font-medium text-white transition-colors hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {isSaving ? (
        <span className="flex items-center justify-center gap-2">
          <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24">
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
              fill="none"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
          {t('Saving...')}
        </span>
      ) : (
        t('Save Changes')
      )}
    </button>
  </div>
);

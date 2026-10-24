import { t } from '../../i18n';

interface ToggleSwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  color?: 'sky' | 'emerald';
  ariaLabel?: string;
}

const colorClasses = {
  sky: { active: 'bg-sky-600', ring: 'focus:ring-sky-500/50' },
  emerald: { active: 'bg-emerald-600', ring: 'focus:ring-emerald-500/50' },
};

export const ToggleSwitch = ({
  checked,
  onChange,
  disabled = false,
  color = 'sky',
  ariaLabel = t('Toggle switch'),
}: ToggleSwitchProps) => {
  const { active, ring } = colorClasses[color];

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      onClick={() => !disabled && onChange(!checked)}
      disabled={disabled}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors duration-200 focus:ring-2 focus:outline-hidden ${ring} disabled:cursor-not-allowed disabled:opacity-60 ${checked ? active : 'bg-gray-300 dark:bg-gray-600'}`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow-xs transition-transform duration-200 ${checked ? 'translate-x-6' : 'translate-x-1'}`}
      />
    </button>
  );
};

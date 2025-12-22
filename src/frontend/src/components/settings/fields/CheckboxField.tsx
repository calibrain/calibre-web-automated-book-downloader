import { CheckboxFieldConfig } from '../../../types/settings';

interface CheckboxFieldProps {
  field: CheckboxFieldConfig;
  value: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean; // Override for dynamic disabled state
}

export const CheckboxField = ({ field: _field, value, onChange, disabled }: CheckboxFieldProps) => {
  // disabled prop is already computed by SettingsContent.getDisabledState()
  const isDisabled = disabled ?? false;

  return (
    <button
      type="button"
      role="switch"
      aria-checked={value}
      onClick={() => !isDisabled && onChange(!value)}
      disabled={isDisabled}
      className={`relative inline-flex h-6 w-11 items-center rounded-full
                  transition-colors duration-200 focus:outline-none focus:ring-2
                  focus:ring-sky-500/50 disabled:opacity-60 disabled:cursor-not-allowed
                  ${value ? 'bg-sky-600' : 'bg-gray-300 dark:bg-gray-600'}`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white
                    shadow-sm transition-transform duration-200
                    ${value ? 'translate-x-6' : 'translate-x-1'}`}
      />
    </button>
  );
};

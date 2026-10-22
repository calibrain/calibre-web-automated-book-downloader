import { t } from '../../../i18n';
import type { TextFieldConfig } from '../../../types/settings';

interface TextFieldProps {
  field: TextFieldConfig;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

export const TextField = ({ field, value, onChange, disabled }: TextFieldProps) => {
  // disabled prop is already computed by SettingsContent.getDisabledState()
  const isDisabled = disabled ?? false;

  return (
    <input
      type="text"
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value)}
      placeholder={field.placeholder}
      aria-label={field.label || field.placeholder || t('Text field')}
      maxLength={field.maxLength}
      disabled={isDisabled}
      className="w-full rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-2 text-sm transition-colors focus:border-sky-500 focus:ring-2 focus:ring-sky-500/50 focus:outline-hidden disabled:cursor-not-allowed disabled:opacity-60"
    />
  );
};

import { TextFieldConfig } from '../../../types/settings';

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
      maxLength={field.maxLength}
      disabled={isDisabled}
      className="w-full px-3 py-2 rounded-lg border border-[var(--border-muted)]
                 bg-[var(--bg-soft)] text-sm
                 focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500
                 disabled:opacity-60 disabled:cursor-not-allowed
                 transition-colors"
    />
  );
};

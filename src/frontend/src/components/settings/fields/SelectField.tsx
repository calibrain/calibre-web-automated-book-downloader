import { SelectFieldConfig } from '../../../types/settings';

interface SelectFieldProps {
  field: SelectFieldConfig;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

export const SelectField = ({ field, value, onChange, disabled }: SelectFieldProps) => {
  // disabled prop is already computed by SettingsContent.getDisabledState()
  const isDisabled = disabled ?? false;

  // Use field's default value as fallback when value is empty
  const effectiveValue = value || field.default || '';

  return (
    <select
      value={effectiveValue}
      onChange={(e) => onChange(e.target.value)}
      disabled={isDisabled}
      className="w-full px-3 py-2 rounded-lg border border-[var(--border-muted)]
                 bg-[var(--bg-soft)] text-sm appearance-none
                 focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500
                 disabled:opacity-60 disabled:cursor-not-allowed
                 transition-colors pr-10"
      style={{
        backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3E%3Cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='m6 8 4 4 4-4'/%3E%3C/svg%3E")`,
        backgroundPosition: 'right 0.5rem center',
        backgroundRepeat: 'no-repeat',
        backgroundSize: '1.5em 1.5em',
      }}
    >
      {!effectiveValue && (
        <option value="" disabled hidden>
          Select...
        </option>
      )}
      {field.options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
};

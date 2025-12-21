import { NumberFieldConfig } from '../../../types/settings';

interface NumberFieldProps {
  field: NumberFieldConfig;
  value: number;
  onChange: (value: number) => void;
  disabled?: boolean;
}

export const NumberField = ({ field, value, onChange, disabled }: NumberFieldProps) => {
  // disabled prop is already computed by SettingsContent.getDisabledState()
  const isDisabled = disabled ?? false;

  return (
    <input
      type="number"
      value={value ?? field.min ?? 0}
      onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
      min={field.min}
      max={field.max}
      step={field.step ?? 1}
      disabled={isDisabled}
      className="w-full px-3 py-2 rounded-lg border border-[var(--border-muted)]
                 bg-[var(--bg-soft)] text-sm
                 focus:outline-none focus:ring-2 focus:ring-sky-500/50 focus:border-sky-500
                 disabled:opacity-60 disabled:cursor-not-allowed
                 transition-colors"
    />
  );
};

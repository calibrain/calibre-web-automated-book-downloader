import { t } from '../../../i18n';
import type { NumberFieldConfig } from '../../../types/settings';

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
      aria-label={field.label || t('Number field')}
      disabled={isDisabled}
      className="w-full rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-2 text-sm transition-colors focus:border-sky-500 focus:ring-2 focus:ring-sky-500/50 focus:outline-hidden disabled:cursor-not-allowed disabled:opacity-60"
    />
  );
};

import { SelectFieldConfig } from '../../../types/settings';
import { DropdownList } from '../../DropdownList';

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

  // Convert options to DropdownList format
  const dropdownOptions = field.options.map((opt) => ({
    value: opt.value,
    label: opt.label,
  }));

  const handleChange = (newValue: string | string[]) => {
    // DropdownList may return string or string[] - we expect string for single select
    const val = Array.isArray(newValue) ? newValue[0] ?? '' : newValue;
    onChange(val);
  };

  if (isDisabled) {
    // When disabled, show a static display instead of the dropdown
    const selectedOption = field.options.find((opt) => opt.value === effectiveValue);
    return (
      <div className="w-full px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] text-sm opacity-60 cursor-not-allowed">
        {selectedOption?.label || 'Select...'}
      </div>
    );
  }

  return (
    <DropdownList
      options={dropdownOptions}
      value={effectiveValue}
      onChange={handleChange}
      placeholder="Select..."
      widthClassName="w-full"
    />
  );
};

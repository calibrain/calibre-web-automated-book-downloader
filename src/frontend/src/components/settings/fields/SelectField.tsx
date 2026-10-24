import { useMemo } from 'react';

import { t } from '../../../i18n';
import type { SelectFieldConfig } from '../../../types/settings';
import { DropdownList } from '../../DropdownList';

interface SelectFieldProps {
  field: SelectFieldConfig;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  filterValue?: string;
}

export const SelectField = ({
  field,
  value,
  onChange,
  disabled,
  filterValue,
}: SelectFieldProps) => {
  const isDisabled = disabled ?? false;

  const normalizedOptions = useMemo(
    () =>
      field.options.map((opt) => ({
        ...opt,
        value: opt.value,
        childOf: opt.childOf,
        label: opt.label ?? opt.value,
      })),
    [field.options],
  );

  // Filter options based on filterValue (cascading dropdown support)
  const filteredOptions = useMemo(() => {
    if (!filterValue) {
      return normalizedOptions.filter((opt) => !opt.childOf);
    }
    // Filter to options that belong to the selected parent or have no parent
    return normalizedOptions.filter((opt) => !opt.childOf || opt.childOf === filterValue);
  }, [normalizedOptions, filterValue]);

  // Use field's default value as fallback when value is empty
  const effectiveValue = value || field.default || '';

  // Convert options to DropdownList format
  const dropdownOptions = filteredOptions.map((opt) => ({
    value: opt.value,
    label: opt.label,
    description: opt.description,
  }));

  const handleChange = (newValue: string | string[]) => {
    // DropdownList may return string or string[] - we expect string for single select
    const val = Array.isArray(newValue) ? (newValue[0] ?? '') : newValue;
    onChange(val);
  };

  if (isDisabled) {
    // When disabled, show a static display instead of the dropdown
    const selectedOption = filteredOptions.find((opt) => opt.value === effectiveValue);
    return (
      <div className="w-full cursor-not-allowed rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-2 text-sm opacity-60">
        {selectedOption?.label || t('Select...')}
      </div>
    );
  }

  return (
    <DropdownList
      options={dropdownOptions}
      value={effectiveValue}
      onChange={handleChange}
      placeholder={t('Select...')}
      widthClassName="w-full"
    />
  );
};

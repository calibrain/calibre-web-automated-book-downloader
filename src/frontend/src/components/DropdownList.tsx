import { ReactNode } from 'react';
import { Dropdown } from './Dropdown';

export interface DropdownListOption {
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
  icon?: ReactNode;
}

interface DropdownListProps {
  label?: string;
  placeholder?: string;
  options: DropdownListOption[];
  multiple?: boolean;
  showCheckboxes?: boolean;
  value: string[] | string | null | undefined;
  onChange: (value: string[] | string) => void;
  align?: 'left' | 'right';
  widthClassName?: string;
  buttonClassName?: string;
  summaryFormatter?: (selected: DropdownListOption[], placeholder: string) => ReactNode;
  keepOpenOnSelect?: boolean;
}

export const DropdownList = ({
  label,
  placeholder = 'Select an option',
  options,
  multiple = false,
  showCheckboxes,
  value,
  onChange,
  align,
  widthClassName,
  buttonClassName,
  summaryFormatter,
  keepOpenOnSelect,
}: DropdownListProps) => {
  const selectedValues = normalizeValue(value, multiple);
  const selectedOptions = options.filter(opt => selectedValues.includes(opt.value));
  const checkboxEnabled = showCheckboxes ?? multiple;
  const stayOpenOnSelect = keepOpenOnSelect ?? multiple;

  const renderSummary = () => {
    if (summaryFormatter) {
      return summaryFormatter(selectedOptions, placeholder);
    }

    if (selectedOptions.length === 0) {
      return <span className="opacity-60 text-base">{placeholder}</span>;
    }

    if (!multiple) {
      return selectedOptions[0]?.label ?? placeholder;
    }

    if (selectedOptions.length === 1) {
      return selectedOptions[0].label;
    }

    const [first, second, ...rest] = selectedOptions.map(opt => opt.label);
    const suffix = rest.length > 0 ? ` +${rest.length}` : '';
    return `${first}, ${second ?? ''}${suffix}`.trim();
  };

  const handleOptionClick = (option: DropdownListOption, close: () => void) => {
    if (option.disabled) return;

    if (multiple) {
      const next = selectedValues.includes(option.value)
        ? selectedValues.filter(v => v !== option.value)
        : [...selectedValues, option.value];
      onChange(next);
      if (!stayOpenOnSelect) {
        close();
      }
      return;
    }

    if (selectedValues[0] === option.value) {
      close();
      return;
    }

    onChange(option.value);
    close();
  };

  return (
    <Dropdown
      label={label}
      summary={renderSummary()}
      align={align}
      widthClassName={widthClassName}
      buttonClassName={buttonClassName}
    >
      {({ close }) => (
        <div role="listbox" aria-multiselectable={multiple}>
          {options.map(option => (
            <button
              type="button"
              key={option.value}
              className={`w-full px-3 py-2 text-left text-base flex items-center gap-2 hover-surface ${
                option.disabled ? 'opacity-50 cursor-not-allowed' : ''
              }`}
              onClick={() => handleOptionClick(option, close)}
              disabled={option.disabled}
            >
              {checkboxEnabled && (
                <input
                  type="checkbox"
                  checked={selectedValues.includes(option.value)}
                  readOnly
                  className="h-4 w-4 rounded border-gray-300 text-sky-600 focus:ring-sky-500 pointer-events-none"
                />
              )}
              {option.icon}
              <div className="flex flex-col">
                <span className="text-base">{option.label}</span>
                {option.description && (
                  <span className="text-xs opacity-70">{option.description}</span>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
    </Dropdown>
  );
};

const normalizeValue = (value: string[] | string | null | undefined, multiple: boolean): string[] => {
  if (multiple) {
    if (Array.isArray(value)) {
      return value;
    }
    if (typeof value === 'string') {
      return [value];
    }
    return [];
  }

  if (Array.isArray(value)) {
    return value.length ? [value[0]] : [];
  }

  if (typeof value === 'string' && value) {
    return [value];
  }

  return [];
};


import type { ReactNode } from 'react';

import { t } from '../i18n';
import { Dropdown } from './Dropdown';

export interface DropdownListOption {
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
  icon?: ReactNode;
  group?: string;
}

interface DropdownListProps {
  label?: string;
  placeholder?: string;
  options: DropdownListOption[];
  multiple?: boolean;
  showCheckboxes?: boolean;
  value: string[] | string | null | undefined;
  onChange: (value: string[] | string) => void;
  align?: 'left' | 'right' | 'auto';
  widthClassName?: string;
  buttonClassName?: string;
  panelClassName?: string;
  summaryFormatter?: (selected: DropdownListOption[], placeholder: string) => ReactNode;
  keepOpenOnSelect?: boolean;
  triggerChrome?: 'default' | 'minimal';
  positionStrategy?: 'absolute' | 'fixed';
  renderTrigger?: (props: { isOpen: boolean; toggle: () => void }) => ReactNode;
  onOpenChange?: (isOpen: boolean) => void;
}

export const DropdownList = ({
  label,
  placeholder = t('Select an option'),
  options,
  multiple = false,
  showCheckboxes,
  value,
  onChange,
  align,
  widthClassName,
  buttonClassName,
  panelClassName,
  summaryFormatter,
  keepOpenOnSelect,
  triggerChrome = 'default',
  positionStrategy,
  renderTrigger,
  onOpenChange,
}: DropdownListProps) => {
  const selectedValues = normalizeValue(value, multiple);
  const selectedOptions = options.filter((opt) => selectedValues.includes(opt.value));
  const checkboxEnabled = showCheckboxes ?? multiple;
  const stayOpenOnSelect = keepOpenOnSelect ?? multiple;

  const renderSummary = () => {
    if (summaryFormatter) {
      return summaryFormatter(selectedOptions, placeholder);
    }

    if (selectedOptions.length === 0) {
      // For single select with empty string value, find and show the empty value option label
      if (!multiple) {
        const emptyOption = options.find((opt) => opt.value === '');
        if (emptyOption) {
          return emptyOption.label;
        }
      }
      return <span className="opacity-60">{placeholder}</span>;
    }

    if (!multiple) {
      return selectedOptions[0]?.label ?? placeholder;
    }

    if (selectedOptions.length === 1) {
      return selectedOptions[0].label;
    }

    const [first, second, ...rest] = selectedOptions.map((opt) => opt.label);
    const suffix = rest.length > 0 ? ` +${rest.length}` : '';
    return `${first}, ${second ?? ''}${suffix}`.trim();
  };

  const handleOptionClick = (option: DropdownListOption, close: () => void) => {
    if (option.disabled) return;

    if (multiple) {
      const next = selectedValues.includes(option.value)
        ? selectedValues.filter((v) => v !== option.value)
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
      panelClassName={panelClassName}
      triggerChrome={triggerChrome}
      positionStrategy={positionStrategy}
      renderTrigger={renderTrigger}
      onOpenChange={onOpenChange}
    >
      {({ close }) => {
        let lastGroup: string | undefined;
        return (
          <div role="listbox" aria-multiselectable={multiple}>
            {options.map((option) => {
              const showGroupHeader = option.group != null && option.group !== lastGroup;
              if (option.group != null) lastGroup = option.group;
              return (
                <div key={option.value}>
                  {showGroupHeader && (
                    <div className="px-3 pt-2 pb-1 text-xs font-medium tracking-wide uppercase opacity-60 select-none">
                      {option.group && t(option.group)}
                    </div>
                  )}
                  <button
                    type="button"
                    className={`hover-surface flex w-full items-center gap-2 px-3 py-2 text-left text-sm ${
                      option.disabled ? 'cursor-not-allowed opacity-50' : ''
                    }`}
                    onClick={() => handleOptionClick(option, close)}
                    disabled={option.disabled}
                  >
                    {checkboxEnabled && (
                      <input
                        type="checkbox"
                        checked={selectedValues.includes(option.value)}
                        readOnly
                        aria-label={option.label}
                        className="pointer-events-none h-4 w-4 rounded-sm border-gray-300 text-sky-600 focus:ring-sky-500"
                      />
                    )}
                    {option.icon}
                    <div className="flex flex-col">
                      <span>{option.label}</span>
                      {option.description && (
                        <span className="text-xs opacity-70">{option.description}</span>
                      )}
                    </div>
                  </button>
                </div>
              );
            })}
          </div>
        );
      }}
    </Dropdown>
  );
};

const normalizeValue = (
  value: string[] | string | null | undefined,
  multiple: boolean,
): string[] => {
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

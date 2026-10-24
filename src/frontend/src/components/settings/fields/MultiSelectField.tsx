import { useLayoutEffect, useRef, useState } from 'react';

import { t } from '../../../i18n';
import type { MultiSelectFieldConfig } from '../../../types/settings';
import { DropdownList } from '../../DropdownList';

interface MultiSelectFieldProps {
  field: MultiSelectFieldConfig;
  value: string[];
  onChange: (value: string[]) => void;
  disabled?: boolean;
}

// Threshold for when to enable collapsible behavior
const COLLAPSE_THRESHOLD_OPTIONS = 12;
// Approximate height for ~4 rows of pills (pills are ~32px + 8px gap)
const COLLAPSED_HEIGHT = 156;
const ALL_OPTION_VALUE = 'all';
const EMPTY_SELECTION: string[] = [];

interface OptionOrderState {
  fieldKey: string;
  optionsIdentity: string;
  selectionIdentity: string;
  pendingInternalSelectionIdentity: string | null;
  sortedOptions: MultiSelectFieldConfig['options'];
}

/**
 * Sort options with selected items first, preserving relative order within each group
 */
const sortOptionsWithSelectedFirst = (
  options: MultiSelectFieldConfig['options'],
  selectedValues: string[],
): MultiSelectFieldConfig['options'] => {
  const selectedSet = new Set(selectedValues);
  const selectedOptions = options.filter((opt) => selectedSet.has(opt.value));
  const unselectedOptions = options.filter((opt) => !selectedSet.has(opt.value));
  return [...selectedOptions, ...unselectedOptions];
};

const getOptionsIdentity = (options: MultiSelectFieldConfig['options']): string =>
  options.map((opt) => `${opt.value}\u0000${opt.label}\u0000${opt.childOf ?? ''}`).join('\u0001');

const getSelectionIdentity = (values: string[]): string =>
  values.toSorted((left, right) => left.localeCompare(right)).join('\u0001');

interface MultiSelectVariantProps {
  field: MultiSelectFieldConfig;
  selected: string[];
  onChange: (value: string[]) => void;
  isDisabled: boolean;
}

// Dropdown variant - use DropdownList with checkboxes
const MultiSelectDropdownField = ({
  field,
  selected,
  onChange,
  isDisabled,
}: MultiSelectVariantProps) => {
  const optionValues = field.options.map((opt) => opt.value);
  const optionSet = new Set(optionValues);
  const hasAllOption = optionSet.has(ALL_OPTION_VALUE);
  const orderedOptions = hasAllOption
    ? [
        ...field.options.filter((opt) => opt.value === ALL_OPTION_VALUE),
        ...field.options.filter((opt) => opt.value !== ALL_OPTION_VALUE),
      ]
    : field.options;
  const nonAllValues = orderedOptions
    .map((opt) => opt.value)
    .filter((optValue) => optValue !== ALL_OPTION_VALUE);

  const normalizeValues = (values: string[]): string[] => {
    const deduped = new Set(
      values
        .map((entry) => entry.trim())
        .filter((entry) => entry.length > 0 && optionSet.has(entry)),
    );
    return orderedOptions.map((opt) => opt.value).filter((optValue) => deduped.has(optValue));
  };

  const selectedExplicit = normalizeValues(selected);
  const allSelected =
    hasAllOption &&
    (selectedExplicit.includes(ALL_OPTION_VALUE) ||
      (nonAllValues.length > 0 &&
        nonAllValues.every((optValue) => selectedExplicit.includes(optValue))));

  // Build parent -> children map for cascading selection
  const parentChildMap = new Map<string, string[]>();
  orderedOptions.forEach((opt) => {
    if (opt.childOf) {
      const children = parentChildMap.get(opt.childOf) || [];
      children.push(opt.value);
      parentChildMap.set(opt.childOf, children);
    }
  });

  // Check which children are implicitly selected via parent
  const selectedForCascade = allSelected
    ? selectedExplicit.filter((optValue) => optValue !== ALL_OPTION_VALUE)
    : selectedExplicit;
  const implicitlySelected = new Set<string>();
  selectedForCascade.forEach((val) => {
    const children = parentChildMap.get(val);
    if (children) {
      children.forEach((child) => implicitlySelected.add(child));
    }
  });

  // Build options with disabled state for implicitly selected children
  const dropdownOptions = orderedOptions.map((opt) => ({
    value: opt.value,
    label: opt.label,
    disabled: !allSelected && implicitlySelected.has(opt.value),
  }));

  // For display purposes:
  // - if "all" is active, check every option
  // - otherwise show explicit + implicit parent/child selections
  const displayValue = allSelected
    ? [ALL_OPTION_VALUE, ...nonAllValues]
    : normalizeValues([...selectedExplicit, ...Array.from(implicitlySelected)]);

  const handleDropdownChange = (newValue: string | string[]) => {
    const nextValues = normalizeValues(Array.isArray(newValue) ? newValue : [newValue]);

    if (hasAllOption) {
      const includesAll = nextValues.includes(ALL_OPTION_VALUE);

      // When currently "all" is active:
      // - unticking "all" clears everything
      // - unticking a specific option converts to explicit subset
      if (allSelected && !includesAll && nextValues.length === nonAllValues.length) {
        onChange([]);
        return;
      }
      if (allSelected && includesAll && nextValues.length < optionValues.length) {
        onChange(nextValues.filter((entry) => entry !== ALL_OPTION_VALUE));
        return;
      }

      if (includesAll) {
        onChange([ALL_OPTION_VALUE]);
        return;
      }

      // If user selects every specific option individually, collapse to "all".
      if (
        nonAllValues.length > 0 &&
        nonAllValues.every((optValue) => nextValues.includes(optValue))
      ) {
        onChange([ALL_OPTION_VALUE]);
        return;
      }
    }

    // Filter out implicitly selected values - only store explicit selections.
    const explicitOnly = nextValues.filter((entry) => !implicitlySelected.has(entry));
    onChange(explicitOnly);
  };

  // Custom summary formatter - only count explicit selections
  const summaryFormatter = () => {
    if (allSelected) {
      return orderedOptions.find((opt) => opt.value === ALL_OPTION_VALUE)?.label || t('All');
    }
    if (selectedExplicit.length === 0) {
      return <span className="opacity-60">{field.placeholder || t('Select categories...')}</span>;
    }
    const selectedLabels = selectedExplicit
      .map((v) => orderedOptions.find((o) => o.value === v)?.label)
      .filter(Boolean);
    if (selectedLabels.length === 1) {
      return selectedLabels[0];
    }
    const [first, second, ...rest] = selectedLabels;
    const suffix = rest.length > 0 ? ` +${rest.length}` : '';
    return `${first}, ${second ?? ''}${suffix}`.trim();
  };

  if (isDisabled) {
    return (
      <div className="w-full cursor-not-allowed rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-2 text-sm opacity-60">
        {summaryFormatter()}
      </div>
    );
  }

  return (
    <DropdownList
      options={dropdownOptions}
      value={displayValue}
      onChange={handleDropdownChange}
      multiple
      showCheckboxes
      keepOpenOnSelect
      placeholder={field.placeholder || t('Select categories...')}
      widthClassName="w-full"
      summaryFormatter={summaryFormatter}
    />
  );
};

// Pill variant - inline toggle buttons that collapse past a threshold
const MultiSelectPillsField = ({
  field,
  selected,
  onChange,
  isDisabled,
}: MultiSelectVariantProps) => {
  const [isExpanded, setIsExpanded] = useState(false);
  // Initialize based on option count to avoid flash of expanded content
  const [needsCollapse, setNeedsCollapse] = useState(
    () => field.options.length > COLLAPSE_THRESHOLD_OPTIONS,
  );
  const containerRef = useRef<HTMLDivElement>(null);
  const optionsIdentity = getOptionsIdentity(field.options);
  const selectionIdentity = getSelectionIdentity(selected);
  const [optionOrderState, setOptionOrderState] = useState<OptionOrderState>(() => ({
    fieldKey: field.key,
    optionsIdentity,
    selectionIdentity,
    pendingInternalSelectionIdentity: null,
    sortedOptions: sortOptionsWithSelectedFirst(field.options, selected),
  }));
  if (
    optionOrderState.fieldKey !== field.key ||
    optionOrderState.optionsIdentity !== optionsIdentity
  ) {
    setOptionOrderState({
      fieldKey: field.key,
      optionsIdentity,
      selectionIdentity,
      pendingInternalSelectionIdentity: null,
      sortedOptions: sortOptionsWithSelectedFirst(field.options, selected),
    });
  } else if (optionOrderState.selectionIdentity !== selectionIdentity) {
    const isInternalToggleConfirmation =
      optionOrderState.pendingInternalSelectionIdentity !== null &&
      optionOrderState.pendingInternalSelectionIdentity === selectionIdentity;

    setOptionOrderState((current) => ({
      ...current,
      selectionIdentity,
      pendingInternalSelectionIdentity: null,
      sortedOptions: isInternalToggleConfirmation
        ? current.sortedOptions
        : sortOptionsWithSelectedFirst(field.options, selected),
    }));
  }
  const sortedOptions = optionOrderState.sortedOptions;

  // Verify collapse need after render (handles edge cases where few options still fit)
  useLayoutEffect(() => {
    if (containerRef.current) {
      if (field.options.length > COLLAPSE_THRESHOLD_OPTIONS) {
        const scrollHeight = containerRef.current.scrollHeight;
        setNeedsCollapse(scrollHeight > COLLAPSED_HEIGHT + 20);
      } else {
        setNeedsCollapse(false);
      }
    }
  }, [field.options.length]);

  const toggleOption = (optValue: string) => {
    if (isDisabled) return;
    let newValue: string[];
    if (selected.includes(optValue)) {
      newValue = selected.filter((v) => v !== optValue);
    } else {
      newValue = [...selected, optValue];
    }
    setOptionOrderState((current) => ({
      ...current,
      pendingInternalSelectionIdentity: getSelectionIdentity(newValue),
    }));
    onChange(newValue);
  };

  const isCollapsible = needsCollapse;
  const isCollapsed = isCollapsible && !isExpanded;

  return (
    <div>
      {/* Container with optional max-height constraint */}
      <div className="relative">
        <div
          ref={containerRef}
          className={`flex flex-wrap gap-2 transition-[max-height] duration-300 ease-in-out ${
            isCollapsed ? 'overflow-hidden' : ''
          }`}
          style={{
            maxHeight: isCollapsed ? `${COLLAPSED_HEIGHT}px` : '2000px',
          }}
        >
          {sortedOptions.map((opt) => {
            const isSelected = selected.includes(opt.value);
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => toggleOption(opt.value)}
                disabled={isDisabled}
                className={`rounded-full border px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
                  isSelected
                    ? 'border-sky-600 bg-sky-600 text-white'
                    : 'border-(--border-muted) bg-transparent hover:bg-(--hover-surface)'
                }`}
              >
                {opt.label}
              </button>
            );
          })}
        </div>

        {/* Gradient fade overlay when collapsed */}
        {isCollapsed && (
          <div
            className="pointer-events-none absolute right-0 bottom-0 left-0 h-20"
            style={{
              background: 'linear-gradient(to top, var(--bg) 0%, transparent 85%)',
            }}
          />
        )}
      </div>

      {/* Expand/Collapse toggle - outside the relative container */}
      {isCollapsible && (
        <button
          type="button"
          onClick={() => setIsExpanded(!isExpanded)}
          className="mt-2 flex items-center gap-1 text-sm text-sky-500 transition-colors hover:text-sky-400"
        >
          {isExpanded ? (
            <>
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M5 15l7-7 7 7"
                />
              </svg>
              {t('Show less')}
            </>
          ) : (
            <>
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M19 9l-7 7-7-7"
                />
              </svg>
              {t('Show all {count} options', { count: field.options.length })}
            </>
          )}
        </button>
      )}
    </div>
  );
};

export const MultiSelectField = ({
  field,
  value: fieldValue,
  onChange,
  disabled,
}: MultiSelectFieldProps) => {
  const selected = fieldValue ?? EMPTY_SELECTION;
  // disabled prop is already computed by SettingsContent.getDisabledState()
  const isDisabled = disabled ?? false;

  // Each variant is its own component so neither calls hooks conditionally.
  if (field.variant === 'dropdown') {
    return (
      <MultiSelectDropdownField
        field={field}
        selected={selected}
        onChange={onChange}
        isDisabled={isDisabled}
      />
    );
  }

  return (
    <MultiSelectPillsField
      field={field}
      selected={selected}
      onChange={onChange}
      isDisabled={isDisabled}
    />
  );
};

import { useState, useRef, useEffect } from 'react';
import { MultiSelectFieldConfig } from '../../../types/settings';
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

/**
 * Sort options with selected items first, preserving relative order within each group
 */
const sortOptionsWithSelectedFirst = (
  options: MultiSelectFieldConfig['options'],
  selectedValues: string[]
): MultiSelectFieldConfig['options'] => {
  const selectedSet = new Set(selectedValues);
  const selectedOptions = options.filter((opt) => selectedSet.has(opt.value));
  const unselectedOptions = options.filter((opt) => !selectedSet.has(opt.value));
  return [...selectedOptions, ...unselectedOptions];
};

export const MultiSelectField = ({ field, value, onChange, disabled }: MultiSelectFieldProps) => {
  const selected = value ?? [];
  // disabled prop is already computed by SettingsContent.getDisabledState()
  const isDisabled = disabled ?? false;

  // Dropdown variant - use DropdownList with checkboxes
  if (field.variant === 'dropdown') {
    // Build parent -> children map for cascading selection
    const parentChildMap = new Map<string, string[]>();
    field.options.forEach((opt) => {
      if (opt.childOf) {
        const children = parentChildMap.get(opt.childOf) || [];
        children.push(opt.value);
        parentChildMap.set(opt.childOf, children);
      }
    });

    // Check which children are implicitly selected via parent
    const implicitlySelected = new Set<string>();
    selected.forEach((val) => {
      const children = parentChildMap.get(val);
      if (children) {
        children.forEach((child) => implicitlySelected.add(child));
      }
    });

    // Build options with disabled state for implicitly selected children
    const dropdownOptions = field.options.map((opt) => ({
      value: opt.value,
      label: opt.label,
      disabled: implicitlySelected.has(opt.value),
    }));

    // For display purposes, show both explicit and implicit selections
    const displayValue = [...selected, ...Array.from(implicitlySelected)];

    const handleDropdownChange = (newValue: string | string[]) => {
      const arr = Array.isArray(newValue) ? newValue : [newValue];
      // Filter out implicitly selected values - only store explicit selections
      const explicitOnly = arr.filter((v) => !implicitlySelected.has(v));
      onChange(explicitOnly);
    };

    // Custom summary formatter - only count explicit selections
    const summaryFormatter = () => {
      if (selected.length === 0) {
        return <span className="opacity-60">Select categories...</span>;
      }
      const selectedLabels = selected
        .map((v) => field.options.find((o) => o.value === v)?.label)
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
        <div className="w-full px-3 py-2 rounded-lg border border-[var(--border-muted)] bg-[var(--bg-soft)] text-sm opacity-60 cursor-not-allowed">
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
        placeholder="Select categories..."
        widthClassName="w-full"
        summaryFormatter={summaryFormatter}
      />
    );
  }
  const [isExpanded, setIsExpanded] = useState(false);
  // Initialize based on option count to avoid flash of expanded content
  const [needsCollapse, setNeedsCollapse] = useState(
    () => field.options.length > COLLAPSE_THRESHOLD_OPTIONS
  );
  const containerRef = useRef<HTMLDivElement>(null);

  // Track the last value we set via onChange to detect external changes
  const lastInternalValueRef = useRef<string[] | null>(null);

  // Sorted options - initialized with selected items first, updated only on external changes
  const [sortedOptions, setSortedOptions] = useState(() =>
    sortOptionsWithSelectedFirst(field.options, selected)
  );

  // Detect external value changes (like after save or initial load) and re-sort
  useEffect(() => {
    // If the value changed and it's not from our own onChange call, re-sort
    const lastInternal = lastInternalValueRef.current;
    const isExternalChange =
      lastInternal === null || // Initial mount
      lastInternal.length !== selected.length ||
      !lastInternal.every((v) => selected.includes(v));

    // Only re-sort if the change wasn't triggered by user interaction
    if (isExternalChange && lastInternal !== null) {
      // Check if this is truly external (values differ in a way that suggests a save/reset)
      const wasInternalToggle =
        Math.abs(lastInternal.length - selected.length) === 1 &&
        (lastInternal.every((v) => selected.includes(v)) ||
          selected.every((v) => lastInternal.includes(v)));

      if (!wasInternalToggle) {
        setSortedOptions(sortOptionsWithSelectedFirst(field.options, selected));
      }
    }
  }, [selected, field.options]);

  // Update sortedOptions when field itself changes (different field or options refreshed)
  useEffect(() => {
    setSortedOptions(sortOptionsWithSelectedFirst(field.options, selected));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally omit 'selected' to avoid re-sorting on user toggle
  }, [field.key, field.options]);

  // Verify collapse need after render (handles edge cases where few options still fit)
  useEffect(() => {
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
    // Track this as an internal change so we don't re-sort
    lastInternalValueRef.current = newValue;
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
                className={`px-3 py-1.5 rounded-full text-sm font-medium
                            transition-colors border
                            disabled:opacity-60 disabled:cursor-not-allowed
                            ${
                              isSelected
                                ? 'bg-sky-600 text-white border-sky-600'
                                : 'bg-transparent border-[var(--border-muted)] hover:bg-[var(--hover-surface)]'
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
            className="absolute bottom-0 left-0 right-0 h-20 pointer-events-none"
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
          className="mt-2 text-sm text-sky-500 hover:text-sky-400
                     transition-colors flex items-center gap-1"
        >
          {isExpanded ? (
            <>
              <svg
                className="w-4 h-4"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M5 15l7-7 7 7"
                />
              </svg>
              Show less
            </>
          ) : (
            <>
              <svg
                className="w-4 h-4"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M19 9l-7 7-7-7"
                />
              </svg>
              Show all {field.options.length} options
            </>
          )}
        </button>
      )}
    </div>
  );
};

import { Language } from '../types';
import {
  LANGUAGE_OPTION_ALL,
  LANGUAGE_OPTION_DEFAULT,
  normalizeLanguageSelection,
} from '../utils/languageFilters';
import { DropdownList, DropdownListOption } from './DropdownList';

interface LanguageMultiSelectProps {
  options: Language[];
  value: string[];
  onChange: (value: string[]) => void;
  defaultLanguageCodes: string[];
  label?: string;
  placeholder?: string;
}

export const LanguageMultiSelect = ({
  options,
  value,
  onChange,
  defaultLanguageCodes,
  label,
  placeholder,
}: LanguageMultiSelectProps) => {
  const defaultCodeSet = new Set(defaultLanguageCodes);

  // Get default languages with their full info
  const defaultLanguages = options.filter(lang => defaultCodeSet.has(lang.code));
  const nonDefaultLanguages = options.filter(lang => !defaultCodeSet.has(lang.code));

  // All selectable values (individual language codes, not LANGUAGE_OPTION_DEFAULT)
  const selectableValues = [...defaultLanguageCodes, ...nonDefaultLanguages.map(lang => lang.code)];

  // Build option list: All, then defaults (marked), then others
  const optionList: DropdownListOption[] = [
    {
      value: LANGUAGE_OPTION_ALL,
      label: 'All languages',
    },
    // Each default language as a separate option
    ...defaultLanguages.map(lang => ({
      value: lang.code,
      label: `${lang.language} (default)`,
    })),
    // Non-default languages
    ...nonDefaultLanguages.map(lang => ({
      value: lang.code,
      label: lang.language,
    })),
  ];

  // Expand LANGUAGE_OPTION_DEFAULT to individual default codes for comparison
  const expandedValue = value.flatMap(v =>
    v === LANGUAGE_OPTION_DEFAULT ? defaultLanguageCodes : [v]
  );

  const includesAllSelection = value.includes(LANGUAGE_OPTION_ALL);
  const effectiveValue = includesAllSelection ? selectableValues : expandedValue;
  const selectedSet = new Set(effectiveValue);
  const isAllSelected = selectableValues.every(code => selectedSet.has(code));
  const displayedValue = isAllSelected ? [LANGUAGE_OPTION_ALL, ...effectiveValue] : effectiveValue;

  const summaryFormatter = (_selected: DropdownListOption[], fallback: string) => {
    if (isAllSelected) {
      return 'All languages';
    }

    const labels: string[] = [];

    // Check each language
    options.forEach(lang => {
      if (selectedSet.has(lang.code)) {
        labels.push(lang.language);
      }
    });

    if (labels.length === 0) {
      return placeholder || fallback;
    }

    if (labels.length === 1) {
      return labels[0];
    }

    const [first, second, ...rest] = labels;
    const suffix = rest.length > 0 ? ` +${rest.length}` : '';
    return `${first}, ${second ?? ''}${suffix}`.trim();
  };

  const handleChange = (nextValue: string[] | string) => {
    const nextArray = Array.isArray(nextValue) ? nextValue : [nextValue];
    const includesAll = nextArray.includes(LANGUAGE_OPTION_ALL);
    const toggledAllOn = includesAll && !isAllSelected;
    const toggledAllOff =
      isAllSelected && !includesAll && nextArray.length === effectiveValue.length;

    let resolved = nextArray.filter(code => code !== LANGUAGE_OPTION_ALL);

    if (toggledAllOn) {
      resolved = [LANGUAGE_OPTION_ALL];
    } else if (toggledAllOff) {
      resolved = [];
    }

    const normalized = normalizeLanguageSelection(resolved);
    onChange(normalized);
  };

  return (
    <DropdownList
      label={label}
      options={optionList}
      multiple
      showCheckboxes
      value={displayedValue}
      onChange={handleChange}
      placeholder={placeholder}
      summaryFormatter={summaryFormatter}
      keepOpenOnSelect
    />
  );
};



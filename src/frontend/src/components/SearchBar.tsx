import type { ComponentType, InputHTMLAttributes, ReactNode } from 'react';
import { forwardRef, useId, useImperativeHandle, useMemo, useRef, useState } from 'react';

import { useSearchMode } from '../contexts/SearchModeContext';
import { useSearchBarAutocomplete } from '../hooks/searchBar/useSearchBarAutocomplete';
import { useDismiss } from '../hooks/useDismiss';
import { useLatestCallback } from '../hooks/useLatestCallback';
import { t } from '../i18n';
import type { DynamicFieldOption } from '../services/api';
import type { ContentType, MetadataSearchField, QueryTargetOption, SortOption } from '../types';
import { SearchBarAutocompleteSession } from './SearchBarAutocompleteSession';
import { SearchBarDynamicOptionsSession } from './SearchBarDynamicOptionsSession';
import { ToggleSwitch } from './shared/ToggleSwitch';

type SearchOptionsLayout = 'popover' | 'inline';

interface SearchBarProps {
  value: string | number | boolean;
  valueLabel?: string;
  onChange: (value: string | number | boolean, label?: string) => void;
  onSubmit: () => void;
  isLoading?: boolean;
  onAdvancedToggle?: () => void;
  isAdvancedActive?: boolean;
  placeholder?: string;
  inputAriaLabel?: string;
  className?: string;
  inputClassName?: string;
  controlsClassName?: string;
  clearButtonLabel?: string;
  clearButtonTitle?: string;
  searchButtonLabel?: string;
  searchButtonTitle?: string;
  autoComplete?: string;
  enterKeyHint?: InputHTMLAttributes<HTMLInputElement>['enterKeyHint'];
  contentType?: ContentType;
  onContentTypeChange?: (type: ContentType) => void;
  allowedContentTypes?: ContentType[];
  combinedMode?: boolean;
  combinedModeLocked?: boolean;
  onCombinedModeChange?: (enabled: boolean) => void;
  queryTargets?: QueryTargetOption[];
  activeQueryTarget?: string;
  onQueryTargetChange?: (target: string) => void;
  activeQueryField?: MetadataSearchField | null;
  disabled?: boolean;
  optionsLayout: SearchOptionsLayout;
}

export interface SearchBarHandle {
  submit: () => void;
}

const EMPTY_SORT_OPTIONS: SortOption[] = [];
const EMPTY_AUTOCOMPLETE_OPTIONS: DynamicFieldOption[] = [];
const EMPTY_QUERY_TARGETS: QueryTargetOption[] = [];

const OPTIONS_LAYOUT_CLASSES: Record<
  SearchOptionsLayout,
  { panel: string; group: string; options: string; option: string }
> = {
  popover: {
    panel: 'flex flex-col divide-y divide-(--border-muted)',
    group: 'flex flex-col gap-2 py-3 first:pt-0 last:pb-0',
    options: 'grid grid-cols-2 gap-2',
    option: '',
  },
  inline: {
    panel:
      'animate-fade-in-down flex flex-col gap-4 px-1 pt-2 sm:flex-row sm:flex-wrap sm:items-start sm:gap-x-8 sm:gap-y-2',
    group: 'flex min-w-0 flex-col gap-2',
    options: 'flex flex-col gap-1 sm:flex-row sm:flex-wrap sm:gap-2',
    option: 'max-sm:border-transparent',
  },
};

const BookIcon = () => (
  <svg
    className="h-5 w-5 shrink-0"
    fill="none"
    viewBox="0 0 24 24"
    strokeWidth="1.5"
    stroke="currentColor"
    aria-hidden="true"
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"
    />
  </svg>
);

const AudiobookIcon = () => (
  <svg
    className="h-5 w-5 shrink-0"
    fill="none"
    viewBox="0 0 24 24"
    strokeWidth="1.5"
    stroke="currentColor"
    aria-hidden="true"
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z"
    />
  </svg>
);

const BothIcon = () => (
  <svg
    className="h-5 w-5"
    fill="none"
    viewBox="0 0 24 24"
    strokeWidth="1.5"
    stroke="currentColor"
    aria-hidden="true"
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M13.19 8.688a4.5 4.5 0 0 1 1.242 7.244l-4.5 4.5a4.5 4.5 0 0 1-6.364-6.364l1.757-1.757m13.35-.622 1.757-1.757a4.5 4.5 0 0 0-6.364-6.364l-4.5 4.5a4.5 4.5 0 0 0 1.242 7.244"
    />
  </svg>
);

const CheckIcon = ({ className = 'w-3.5 h-3.5' }: { className?: string }) => (
  <svg
    className={className}
    fill="none"
    viewBox="0 0 24 24"
    strokeWidth="2.5"
    stroke="currentColor"
    aria-hidden="true"
  >
    <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
  </svg>
);

const CONTENT_TYPE_OPTIONS: { type: ContentType; label: string; Icon: ComponentType }[] = [
  { type: 'ebook', label: 'Books', Icon: BookIcon },
  { type: 'audiobook', label: 'Audiobooks', Icon: AudiobookIcon },
];

const OptionsGroupHeader = ({ label, action }: { label: string; action: ReactNode }) => (
  <div className="flex min-h-7 items-center justify-between px-1">
    <span className="text-xs font-medium opacity-60">{label}</span>
    {action}
  </div>
);

const getDefaultPlaceholder = (
  contentType: ContentType,
  activeQueryTarget: QueryTargetOption | undefined,
  fallback?: string,
  isCombinedMode?: boolean,
): string => {
  if (fallback) return fallback;

  if (!activeQueryTarget || activeQueryTarget.source === 'general') {
    if (isCombinedMode) return t('Search Books & Audiobooks');
    return contentType === 'ebook' ? t('Search Books') : t('Search Audiobooks');
  }

  if (activeQueryTarget.source === 'manual') {
    return t('Search releases directly…');
  }

  const field = activeQueryTarget.field;
  if (field?.placeholder) {
    return t(field.placeholder);
  }

  return t('Search by {field}…', { field: t(activeQueryTarget.label).toLowerCase() });
};

const hasActiveValue = (value: string | number | boolean): boolean => {
  if (typeof value === 'string') {
    return value.trim().length > 0;
  }
  if (typeof value === 'number') {
    return true;
  }
  return value;
};

const getClearedValue = (field?: MetadataSearchField | null): string | boolean => {
  if (field?.type === 'CheckboxSearchField') {
    return false;
  }
  return '';
};

export const SearchBar = forwardRef<SearchBarHandle, SearchBarProps>(
  (
    {
      value,
      valueLabel,
      onChange,
      onSubmit,
      isLoading = false,
      onAdvancedToggle,
      isAdvancedActive = false,
      placeholder,
      inputAriaLabel = t('Search books'),
      className = '',
      inputClassName = '',
      controlsClassName = '',
      clearButtonLabel = t('Clear search input'),
      clearButtonTitle = t('Clear search'),
      searchButtonLabel = t('Search books'),
      searchButtonTitle = t('Search'),
      autoComplete = 'off',
      enterKeyHint = 'search',
      contentType = 'ebook',
      onContentTypeChange,
      allowedContentTypes,
      combinedMode = false,
      combinedModeLocked = false,
      onCombinedModeChange,
      queryTargets = EMPTY_QUERY_TARGETS,
      activeQueryTarget = 'general',
      onQueryTargetChange,
      activeQueryField,
      disabled = false,
      optionsLayout,
    },
    ref,
  ) => {
    const { searchMode } = useSearchMode();
    const inputRef = useRef<HTMLInputElement>(null);
    const buttonRef = useRef<HTMLButtonElement>(null);
    // Deferred submits below run from a timeout, not an Effect, so this is a latest-value
    // callback rather than an Effect Event. See useLatestCallback.
    const submitLatest = useLatestCallback(() => onSubmit());
    const selectorRef = useRef<HTMLDivElement>(null);
    const hasSearchQuery = hasActiveValue(value);
    const [isSelectOpen, setIsSelectOpen] = useState(false);
    const [isAutocompleteOpen, setIsAutocompleteOpen] = useState(false);
    const selectTriggerRef = useRef<HTMLButtonElement>(null);
    const selectPanelRef = useRef<HTMLDivElement>(null);
    const autocompletePanelRef = useRef<HTMLDivElement>(null);
    const optionsPanelId = useId();
    const optionsPanelRef = useRef<HTMLDivElement>(null);
    const isPopover = optionsLayout === 'popover';
    const layoutClasses = OPTIONS_LAYOUT_CLASSES[optionsLayout];
    const [isOptionsOpen, setIsOptionsOpen] = useState(false);
    const closeOptions = () => setIsOptionsOpen(false);

    const hasMultipleContentTypes = !allowedContentTypes || allowedContentTypes.length !== 1;
    const showContentTypeSelector =
      searchMode !== 'direct' && !!onContentTypeChange && hasMultipleContentTypes;
    const showQueryTargetSelector = showContentTypeSelector || queryTargets.length > 1;
    const inputPaddingClass = showQueryTargetSelector ? 'pl-3 rounded-r-full' : 'pl-4 rounded-full';
    const searchInputClass = [
      'w-full min-w-0 py-3 border-0 outline-hidden search-input bg-transparent',
      inputPaddingClass,
    ].join(' ');

    const activeTarget = useMemo(
      () => queryTargets.find((target) => target.key === activeQueryTarget) ?? queryTargets[0],
      [queryTargets, activeQueryTarget],
    );
    const showActiveTargetLabel = queryTargets.length > 0 && activeTarget?.source !== 'general';

    // Manual search browses release sources directly, one media type at a time — the
    // combined ("both") flow doesn't apply. Present a plain, switchable Books/Audiobooks
    // choice for it, even when combined search is forced on for metadata targets.
    const isManualTarget = activeTarget?.source === 'manual';
    const combinedSelectionActive = combinedMode && !isManualTarget;
    const combinedSelectorLocked = combinedModeLocked && !isManualTarget;
    const combinedToggleAvailable = !!onCombinedModeChange && !isManualTarget;

    // The inline panel renders below the bar, outside selectorRef.
    useDismiss(isOptionsOpen, [selectorRef, optionsPanelRef], closeOptions);
    useDismiss(isSelectOpen, [selectPanelRef, selectTriggerRef], () => setIsSelectOpen(false));
    useDismiss(isAutocompleteOpen, [autocompletePanelRef, inputRef], () =>
      setIsAutocompleteOpen(false),
    );

    const dynamicEndpoint =
      activeQueryField?.type === 'DynamicSelectSearchField'
        ? activeQueryField.options_endpoint
        : null;
    const [dynamicOptionsState, setDynamicOptionsState] = useState<{
      endpoint: string;
      options: SortOption[];
    } | null>(null);
    const [autocompleteRequestState, setAutocompleteRequestState] = useState<{
      requestKey: string;
      options: DynamicFieldOption[];
      isLoading: boolean;
    } | null>(null);
    const textSearchField = activeQueryField?.type === 'TextSearchField' ? activeQueryField : null;
    const {
      autocompleteEndpoint,
      autocompleteMinQueryLength,
      textInputValue: autocompleteTextInputValue,
      autocompleteEmptyMessage,
      setAutocompleteDraftValue,
      setAutocompleteSelection,
      resetAutocomplete,
    } = useSearchBarAutocomplete({
      field: textSearchField,
      value,
      valueLabel,
    });
    const dynamicOptions =
      dynamicEndpoint && dynamicOptionsState?.endpoint === dynamicEndpoint
        ? dynamicOptionsState.options
        : EMPTY_SORT_OPTIONS;
    const isDynamicLoading =
      dynamicEndpoint !== null && dynamicOptionsState?.endpoint !== dynamicEndpoint;
    let textInputValue = autocompleteTextInputValue;
    if (!autocompleteEndpoint) {
      textInputValue = typeof value === 'string' ? value : String(value ?? '');
    }
    const normalizedAutocompleteQuery = textInputValue.trim();
    const autocompleteRequestKey =
      autocompleteEndpoint &&
      isAutocompleteOpen &&
      normalizedAutocompleteQuery.length >= autocompleteMinQueryLength
        ? `${autocompleteEndpoint}::${normalizedAutocompleteQuery.toLowerCase()}`
        : null;
    const autocompleteOptions =
      autocompleteRequestKey && autocompleteRequestState?.requestKey === autocompleteRequestKey
        ? autocompleteRequestState.options
        : EMPTY_AUTOCOMPLETE_OPTIONS;
    const isAutocompleteLoading =
      autocompleteRequestKey !== null &&
      (autocompleteRequestState?.requestKey !== autocompleteRequestKey ||
        autocompleteRequestState.isLoading);

    // Resolve options for any select-type field
    const selectOptions: SortOption[] = useMemo(() => {
      if (activeQueryField?.type === 'SelectSearchField') {
        return activeQueryField.options;
      }
      if (activeQueryField?.type === 'DynamicSelectSearchField') {
        return dynamicOptions;
      }
      return [];
    }, [activeQueryField, dynamicOptions]);

    const isSelectField =
      activeQueryField?.type === 'SelectSearchField' ||
      activeQueryField?.type === 'DynamicSelectSearchField';

    useImperativeHandle(ref, () => ({
      submit: () => {
        buttonRef.current?.click();
      },
    }));

    const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') {
        if (disabled) {
          e.preventDefault();
          return;
        }
        onSubmit();
        e.currentTarget.blur();
      }
    };

    const handleClearSearch = () => {
      onChange(getClearedValue(activeQueryField));
      setIsSelectOpen(false);
      setIsAutocompleteOpen(false);
      resetAutocomplete();
      inputRef.current?.focus();
    };

    const handleContentTypeSelect = (type: ContentType) => {
      onContentTypeChange?.(type);
      onCombinedModeChange?.(false);
    };

    const handleCombinedModeToggle = (enabled: boolean) => {
      if (enabled) {
        onContentTypeChange?.('ebook');
      }
      onCombinedModeChange?.(enabled);
    };

    const handleQueryTargetSelect = (targetKey: string) => {
      const nextTarget = queryTargets.find((target) => target.key === targetKey);
      const nextField = nextTarget?.field;
      const shouldOpenSelect =
        nextField?.type === 'SelectSearchField' || nextField?.type === 'DynamicSelectSearchField';

      onQueryTargetChange?.(targetKey);
      setIsSelectOpen(shouldOpenSelect);
      setIsAutocompleteOpen(false);
      resetAutocomplete();
      if (shouldOpenSelect) {
        closeOptions();
      }
    };

    const effectivePlaceholder = getDefaultPlaceholder(
      contentType,
      activeTarget,
      placeholder,
      combinedSelectionActive,
    );
    const effectiveInputAriaLabel = activeTarget
      ? `${inputAriaLabel}: ${t(activeTarget.label)}`
      : inputAriaLabel;

    const selectDropdownOpen = isSelectField && isSelectOpen && selectOptions.length > 0;
    const autocompleteDropdownOpen =
      Boolean(autocompleteEndpoint) &&
      isAutocompleteOpen &&
      textInputValue.trim().length >= autocompleteMinQueryLength;
    const wrapperClasses = ['relative flex items-center rounded-full border', className]
      .filter(Boolean)
      .join(' ')
      .trim();
    const controlsClasses = ['flex items-center gap-1 pr-2 shrink-0', controlsClassName]
      .filter(Boolean)
      .join(' ')
      .trim();

    const renderActiveInput = () => {
      if (!activeQueryField || activeQueryField.type === 'TextSearchField') {
        const inputName = activeQueryField ? `${activeQueryField.key}-search` : 'search-input';
        return (
          <input
            type="search"
            name={inputName}
            placeholder={effectivePlaceholder}
            aria-label={effectiveInputAriaLabel}
            disabled={disabled}
            autoComplete={autoComplete}
            enterKeyHint={enterKeyHint}
            className={[
              searchInputClass,
              'search-input',
              disabled ? 'opacity-60 cursor-not-allowed' : '',
              inputClassName,
            ]
              .filter(Boolean)
              .join(' ')}
            style={{ color: 'var(--text)' }}
            value={textInputValue}
            onChange={(e) => {
              const nextValue = e.target.value;
              if (autocompleteEndpoint) {
                setAutocompleteDraftValue(nextValue);
                setIsAutocompleteOpen(nextValue.trim().length >= autocompleteMinQueryLength);
                setIsSelectOpen(false);
                onChange(nextValue);
                return;
              }
              onChange(nextValue);
            }}
            onFocus={() => {
              if (
                autocompleteEndpoint &&
                textInputValue.trim().length >= autocompleteMinQueryLength
              ) {
                setIsAutocompleteOpen(true);
              }
            }}
            onKeyDown={handleKeyDown}
            ref={inputRef}
          />
        );
      }

      switch (activeQueryField.type) {
        case 'NumberSearchField':
          return (
            <input
              type="number"
              name={`${activeQueryField.key}-search`}
              placeholder={effectivePlaceholder}
              aria-label={effectiveInputAriaLabel}
              disabled={disabled}
              enterKeyHint={enterKeyHint}
              min={activeQueryField.min}
              max={activeQueryField.max}
              step={activeQueryField.step}
              className={[
                searchInputClass,
                disabled ? 'opacity-60 cursor-not-allowed' : '',
                inputClassName,
              ]
                .filter(Boolean)
                .join(' ')}
              style={{ color: 'var(--text)' }}
              value={typeof value === 'number' || typeof value === 'string' ? value : ''}
              onChange={(e) => {
                const raw = e.target.value;
                if (!raw) {
                  onChange('');
                  return;
                }
                const nextValue = Number.parseInt(raw, 10);
                if (!Number.isNaN(nextValue)) {
                  onChange(nextValue);
                }
              }}
              onKeyDown={handleKeyDown}
              ref={inputRef}
            />
          );

        case 'SelectSearchField':
        case 'DynamicSelectSearchField': {
          const currentValue = typeof value === 'string' ? value : String(value ?? '');
          const selectedOption = selectOptions.find((o) => o.value === currentValue);
          let selectTriggerContent = (
            <span className="truncate opacity-50">{effectivePlaceholder}</span>
          );
          if (isDynamicLoading) {
            selectTriggerContent = <span className="truncate opacity-50">{t('Loading…')}</span>;
          } else if (selectedOption) {
            selectTriggerContent = <span className="truncate">{selectedOption.label}</span>;
          }
          return (
            <button
              ref={selectTriggerRef}
              type="button"
              onClick={() => {
                if (!disabled && !isDynamicLoading) {
                  setIsSelectOpen((prev) => !prev);
                  setIsAutocompleteOpen(false);
                }
              }}
              disabled={disabled}
              className={[
                'w-full text-left py-3 flex items-center gap-2',
                showQueryTargetSelector ? 'pl-3' : 'pl-4',
                'pr-2',
                disabled ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer',
              ]
                .filter(Boolean)
                .join(' ')}
              style={{ color: 'var(--text)' }}
              aria-haspopup="listbox"
              aria-expanded={isSelectOpen}
            >
              {selectTriggerContent}
              <svg
                className={`h-3.5 w-3.5 shrink-0 opacity-40 transition-transform duration-200 ${isSelectOpen ? 'rotate-180' : ''}`}
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                strokeWidth="2"
                aria-hidden="true"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
              </svg>
            </button>
          );
        }

        case 'CheckboxSearchField':
          return (
            <label className="flex min-w-0 items-center gap-3 px-4 py-3">
              <input
                type="checkbox"
                checked={Boolean(value)}
                onChange={(e) => onChange(e.target.checked)}
                aria-label={t(activeQueryField.label)}
                className="h-4 w-4 rounded-sm border-(--border-muted) text-emerald-500 focus:ring-emerald-500/50"
              />
              <span className="truncate text-sm" style={{ color: 'var(--text)' }}>
                {t(activeQueryField.label)}
              </span>
            </label>
          );

        default:
          return null;
      }
    };

    let selectorContentTypeLabel = t('audiobooks');
    let selectorIcon = <AudiobookIcon />;
    if (combinedSelectionActive) {
      selectorContentTypeLabel = t('books and audiobooks');
      selectorIcon = <BothIcon />;
    } else if (contentType === 'ebook') {
      selectorContentTypeLabel = t('books');
      selectorIcon = <BookIcon />;
    }

    const activeOptionClasses =
      searchMode === 'direct'
        ? 'border-sky-700/70 bg-sky-700 text-white'
        : 'border-emerald-500/70 bg-emerald-600 text-white';
    const optionClasses = (isActive: boolean) =>
      `flex min-w-0 cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-sm font-medium transition-colors ${
        isActive ? activeOptionClasses : 'hover-surface border-(--border-muted) text-(--text)'
      } ${layoutClasses.option}`;

    const optionsButton = onAdvancedToggle ? (
      <button
        type="button"
        onClick={() => {
          if (isPopover) {
            closeOptions();
          }
          onAdvancedToggle();
        }}
        className={`-mt-1.5 -mr-1 -mb-0.5 flex cursor-pointer items-center gap-1.5 rounded-xl px-4 py-2.5 text-xs font-medium transition-colors ${
          isAdvancedActive ? activeOptionClasses : 'hover-surface text-(--text-muted)'
        }`}
      >
        <svg
          className="h-3.5 w-3.5"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth="1.5"
          stroke="currentColor"
          aria-hidden="true"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M10.5 6h9.75M10.5 6a1.5 1.5 0 1 1-3 0m3 0a1.5 1.5 0 1 0-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-9.75 0h9.75"
          />
        </svg>
        {t('Options')}
      </button>
    ) : null;

    const optionsPanel = (
      <div id={optionsPanelId} ref={optionsPanelRef} className={layoutClasses.panel}>
        {showContentTypeSelector && (
          <div className={layoutClasses.group}>
            <OptionsGroupHeader label={t('Content')} action={optionsButton} />
            <div className={layoutClasses.options}>
              {CONTENT_TYPE_OPTIONS.map(({ type, label, Icon }) => {
                const isActive = contentType === type || combinedSelectionActive;
                return (
                  <button
                    type="button"
                    key={type}
                    onClick={() => handleContentTypeSelect(type)}
                    className={optionClasses(isActive)}
                  >
                    <span className="flex w-4 justify-center">
                      {isActive ? <CheckIcon /> : <Icon />}
                    </span>
                    <span>{t(label)}</span>
                  </button>
                );
              })}
            </div>
            {combinedToggleAvailable && (
              <div className="flex items-center gap-3 px-1 py-1 text-sm font-medium">
                {t('Combined')}
                <ToggleSwitch
                  checked={combinedSelectionActive}
                  onChange={handleCombinedModeToggle}
                  disabled={combinedSelectorLocked}
                  color="emerald"
                  ariaLabel={t('Combined search')}
                />
              </div>
            )}
          </div>
        )}

        {queryTargets.length > 1 && (
          <div className={layoutClasses.group}>
            <OptionsGroupHeader
              label={t('Search by')}
              action={showContentTypeSelector ? null : optionsButton}
            />
            <div className={layoutClasses.options}>
              {queryTargets.map((target) => {
                const isActive = target.key === activeTarget?.key;
                return (
                  <button
                    type="button"
                    key={target.key}
                    onClick={() => handleQueryTargetSelect(target.key)}
                    title={t(target.description || target.label)}
                    aria-label={t(target.label)}
                    className={optionClasses(isActive)}
                  >
                    {isActive && <CheckIcon />}
                    <span className="block truncate">{t(target.label)}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>
    );

    const dynamicOptionsSession = dynamicEndpoint ? (
      <SearchBarDynamicOptionsSession
        key={dynamicEndpoint}
        dynamicEndpoint={dynamicEndpoint}
        onResolved={(options) => {
          setDynamicOptionsState({
            endpoint: dynamicEndpoint,
            options,
          });
        }}
      />
    ) : null;
    const autocompleteSession =
      autocompleteEndpoint && autocompleteRequestKey ? (
        <SearchBarAutocompleteSession
          key={autocompleteRequestKey}
          autocompleteEndpoint={autocompleteEndpoint}
          query={textInputValue}
          minQueryLength={autocompleteMinQueryLength}
          onLoading={(requestKey) => {
            setAutocompleteRequestState({
              requestKey,
              options: EMPTY_AUTOCOMPLETE_OPTIONS,
              isLoading: true,
            });
          }}
          onResolved={(requestKey, options) => {
            setAutocompleteRequestState({
              requestKey,
              options,
              isLoading: false,
            });
          }}
        />
      ) : null;

    return (
      <>
        {autocompleteSession}
        {dynamicOptionsSession}
        <div
          className={wrapperClasses}
          style={{
            background: disabled ? 'var(--bg)' : 'var(--bg-soft)',
            borderColor: 'var(--border-muted)',
          }}
        >
          {showQueryTargetSelector && (
            <div className="relative flex shrink-0 self-stretch" ref={selectorRef}>
              <button
                type="button"
                onClick={() => {
                  setIsOptionsOpen((prev) => !prev);
                  setIsSelectOpen(false);
                  setIsAutocompleteOpen(false);
                }}
                className="hover-action flex cursor-pointer items-center gap-1.5 rounded-l-full pr-2 pl-5 transition-colors"
                style={{ color: 'var(--text)' }}
                aria-label={t('Searching {content} by {target}. Click to change.', {
                  content: selectorContentTypeLabel,
                  target: activeTarget ? t(activeTarget.label) : t('general'),
                })}
                aria-expanded={isOptionsOpen}
                aria-controls={optionsPanelId}
                aria-haspopup={isPopover ? 'dialog' : undefined}
              >
                {selectorIcon}
                {showActiveTargetLabel && (
                  <span className="hidden max-w-24 truncate text-sm font-medium sm:inline">
                    {activeTarget && t(activeTarget.label)}
                  </span>
                )}
                <svg
                  className={`h-3 w-3 opacity-50 transition-transform duration-200 ${isOptionsOpen ? 'rotate-180' : ''}`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  strokeWidth="2.5"
                  aria-hidden="true"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="m19.5 8.25-7.5 7.5-7.5-7.5"
                  />
                </svg>
              </button>
              <div
                className="absolute top-1/2 right-0 h-6 w-px -translate-y-1/2"
                style={{ background: 'var(--border-muted)' }}
              />

              {isPopover && isOptionsOpen && (
                <div
                  className="animate-fade-in-down absolute top-full left-0 z-50 mt-2 w-[min(20rem,calc(100vw-2rem))] overflow-hidden rounded-2xl border border-(--border-muted) bg-(--bg) shadow-2xl"
                  role="dialog"
                  aria-label="Search context"
                >
                  <div className="max-h-[min(24rem,calc(100vh-8rem))] overflow-y-auto p-3">
                    {optionsPanel}
                  </div>
                </div>
              )}
            </div>
          )}

          <div className="min-w-0 flex-1">{renderActiveInput()}</div>

          <div className={controlsClasses}>
            {hasSearchQuery && (
              <button
                type="button"
                onClick={handleClearSearch}
                className="hover-action flex items-center justify-center rounded-full p-2 transition-colors"
                aria-label={clearButtonLabel}
                title={clearButtonTitle}
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth="1.5"
                  stroke="currentColor"
                  className="h-5 w-5"
                  style={{ color: 'var(--text)' }}
                  aria-hidden="true"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                </svg>
              </button>
            )}
            <button
              ref={buttonRef}
              type="button"
              onClick={onSubmit}
              className={`search-bar-button my-2 flex items-center justify-center rounded-full p-2 text-white transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
                searchMode === 'universal'
                  ? 'bg-emerald-600 hover:bg-emerald-700'
                  : 'bg-sky-700 hover:bg-sky-800'
              }`}
              aria-label={searchButtonLabel}
              title={searchButtonTitle}
              disabled={isLoading}
            >
              {!isLoading && (
                <svg
                  className="search-bar-icon h-5 w-5"
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth="2"
                  stroke="currentColor"
                  aria-hidden="true"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
                  />
                </svg>
              )}
              {isLoading && (
                <div className="spinner search-bar-spinner h-5 w-5 border-2 border-white border-t-transparent" />
              )}
            </button>
          </div>

          {selectDropdownOpen && (
            <div
              ref={selectPanelRef}
              className="animate-fade-in-down absolute top-full right-0 left-0 z-50 mt-2 overflow-hidden rounded-2xl border shadow-xl"
              style={{ background: 'var(--bg)', borderColor: 'var(--border-muted)' }}
              role="listbox"
              aria-label={effectiveInputAriaLabel}
            >
              <div className="max-h-64 overflow-y-auto py-1.5">
                {selectOptions.map((option, index) => {
                  const currentValue = typeof value === 'string' ? value : String(value ?? '');
                  const isSelected = option.value === currentValue;
                  const showGroupHeader =
                    option.group != null &&
                    (index === 0 || option.group !== selectOptions[index - 1]?.group);
                  return (
                    <div key={option.value}>
                      {showGroupHeader && (
                        <div className="px-5 pt-2 pb-1 text-xs font-medium tracking-wide uppercase opacity-60 select-none">
                          {option.group && t(option.group)}
                        </div>
                      )}
                      <button
                        type="button"
                        role="option"
                        aria-selected={isSelected}
                        onClick={() => {
                          onChange(option.value, option.label);
                          setIsSelectOpen(false);
                          setTimeout(() => submitLatest(), 0);
                        }}
                        className={`flex w-full items-center gap-3 px-5 py-2.5 text-left text-sm transition-colors ${
                          isSelected ? '' : 'hover-surface'
                        }`}
                        style={{ color: 'var(--text)' }}
                      >
                        <span className={`flex-1 truncate ${isSelected ? 'font-medium' : ''}`}>
                          {option.label}
                        </span>
                        {isSelected && (
                          <svg
                            className="h-4 w-4 shrink-0 text-emerald-500"
                            fill="none"
                            viewBox="0 0 24 24"
                            strokeWidth="2.5"
                            stroke="currentColor"
                            aria-hidden="true"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              d="m4.5 12.75 6 6 9-13.5"
                            />
                          </svg>
                        )}
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {autocompleteDropdownOpen && (
            <div
              ref={autocompletePanelRef}
              className="animate-fade-in-down absolute top-full right-0 left-0 z-50 mt-2 overflow-hidden rounded-2xl border shadow-xl"
              style={{ background: 'var(--bg)', borderColor: 'var(--border-muted)' }}
              role="listbox"
              aria-label={t('{label} suggestions', { label: effectiveInputAriaLabel })}
            >
              <div className="max-h-72 overflow-y-auto py-1.5">
                {isAutocompleteLoading && (
                  <div className="px-5 py-3 text-sm opacity-70" style={{ color: 'var(--text)' }}>
                    {t('Searching…')}
                  </div>
                )}

                {!isAutocompleteLoading && autocompleteOptions.length === 0 && (
                  <div className="px-5 py-3 text-sm opacity-70" style={{ color: 'var(--text)' }}>
                    {autocompleteEmptyMessage}
                  </div>
                )}

                {!isAutocompleteLoading &&
                  autocompleteOptions.map((option) => {
                    const currentValue = typeof value === 'string' ? value : String(value ?? '');
                    const isSelected = option.value === currentValue;

                    return (
                      <button
                        type="button"
                        key={option.value}
                        role="option"
                        aria-selected={isSelected}
                        onClick={() => {
                          setAutocompleteSelection(option.value, option.label);
                          onChange(option.value, option.label);
                          setIsAutocompleteOpen(false);
                          setTimeout(() => submitLatest(), 0);
                        }}
                        className="hover-surface w-full px-5 py-3 text-left text-sm transition-colors"
                        style={{ color: 'var(--text)' }}
                      >
                        <div className="truncate font-medium">{option.label}</div>
                        {option.description && (
                          <div className="mt-0.5 truncate text-xs opacity-70">
                            {option.description}
                          </div>
                        )}
                      </button>
                    );
                  })}
              </div>
            </div>
          )}
        </div>

        {showQueryTargetSelector && !isPopover && isOptionsOpen && optionsPanel}
      </>
    );
  },
);

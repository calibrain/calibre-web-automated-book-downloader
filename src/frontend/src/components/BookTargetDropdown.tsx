import type { ReactNode } from 'react';
import { useCallback, useMemo, useState } from 'react';

import { useMountEffect } from '../hooks/useMountEffect';
import { t } from '../i18n';
import { setBookTargetState, type BookTargetOption } from '../services/api';
import { emitBookTargetChange, onBookTargetChange } from '../utils/bookTargetEvents';
import { loadBookTargets } from '../utils/bookTargetLoader';
import { DropdownList, type DropdownListOption } from './DropdownList';

interface BookTargetDropdownProps {
  provider: string;
  bookId: string;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  widthClassName?: string;
  variant?: 'default' | 'pill' | 'icon';
  align?: 'left' | 'right' | 'auto';
  className?: string;
  onOpenChange?: (isOpen: boolean) => void;
}

const stripCountSuffix = (label: string): string => {
  return label.replace(/\s+\(\d+\)\s*$/, '');
};

const BookmarkIcon = ({ className = 'h-4 w-4' }: { className?: string }) => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    fill="none"
    viewBox="0 0 24 24"
    strokeWidth="1.5"
    stroke="currentColor"
    aria-hidden="true"
    className={`${className} shrink-0`}
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M17.593 3.322c1.1.128 1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0 1 11.186 0Z"
    />
  </svg>
);

const renderSummary = (selectedOptions: DropdownListOption[]) => {
  const count = selectedOptions.length;

  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <BookmarkIcon />
      <span>{count > 0 ? t('Hardcover Lists ({count})', { count }) : t('Hardcover Lists')}</span>
    </span>
  );
};

const STATUS_PREFIX = 'status:';

const isStatusTarget = (value: string): boolean => value.startsWith(STATUS_PREFIX);

const updateOptionChecked = (
  prev: BookTargetOption[],
  target: string,
  checked: boolean,
): BookTargetOption[] =>
  prev.map((option) => {
    if (option.value === target) return { ...option, checked };
    // Statuses are mutually exclusive — uncheck other statuses when one is selected
    if (checked && isStatusTarget(target) && isStatusTarget(option.value)) {
      return { ...option, checked: false };
    }
    return option;
  });

export const BookTargetDropdown = ({
  provider,
  bookId,
  onShowToast,
  widthClassName = 'w-full sm:w-56',
  variant = 'default',
  align = 'auto',
  className,
  onOpenChange,
}: BookTargetDropdownProps) => {
  return (
    <BookTargetDropdownSession
      key={`${provider}:${bookId}`}
      provider={provider}
      bookId={bookId}
      onShowToast={onShowToast}
      widthClassName={widthClassName}
      variant={variant}
      align={align}
      className={className}
      onOpenChange={onOpenChange}
    />
  );
};

const BookTargetDropdownSession = ({
  provider,
  bookId,
  onShowToast,
  widthClassName = 'w-full sm:w-56',
  variant = 'default',
  align = 'auto',
  className,
  onOpenChange,
}: BookTargetDropdownProps) => {
  const [options, setOptions] = useState<BookTargetOption[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pendingTargets, setPendingTargets] = useState(new Set<string>());

  useMountEffect(() => {
    let isMounted = true;

    const run = async () => {
      try {
        const loaded = await loadBookTargets(provider, bookId);
        if (!isMounted) return;
        setOptions(loaded);
        setLoadError(null);
      } catch (error) {
        if (!isMounted) return;
        const message =
          error instanceof Error ? error.message : t('Failed to load Hardcover lists');
        setOptions([]);
        setLoadError(message);
      } finally {
        if (isMounted) {
          setIsLoading(false);
        }
      }
    };

    setLoadError(null);
    setPendingTargets(new Set());
    setIsLoading(true);
    void run();

    return () => {
      isMounted = false;
    };
  });

  // Sync from changes made by other BookTargetDropdown instances for the same book
  useMountEffect(() => {
    return onBookTargetChange((event) => {
      if (event.provider !== provider || event.bookId !== bookId) return;
      setOptions((prev) => updateOptionChecked(prev, event.target, event.selected));
    });
  });

  const selectedValues = useMemo(
    () => options.filter((option) => option.checked).map((option) => option.value),
    [options],
  );

  const dropdownOptions = useMemo<DropdownListOption[]>(() => {
    if (isLoading) {
      return [{ value: '__loading', label: t('Loading…'), disabled: true }];
    }

    if (loadError) {
      return [{ value: '__error', label: loadError, disabled: true }];
    }

    if (options.length === 0) {
      return [{ value: '__empty', label: t('No writable Hardcover targets'), disabled: true }];
    }

    return options.map((option) => ({
      value: option.value,
      label: option.label,
      description: option.description,
      group: option.group,
      disabled: !option.writable || pendingTargets.has(option.value),
    }));
  }, [isLoading, loadError, options, pendingTargets]);

  const handleChange = useCallback(
    (nextValue: string[] | string) => {
      if (!Array.isArray(nextValue)) {
        return;
      }

      const nextSelected = new Set(nextValue);
      const currentSelected = new Set(selectedValues);
      const toggledTarget =
        nextValue.find((value) => !currentSelected.has(value)) ??
        selectedValues.find((value) => !nextSelected.has(value));

      if (!toggledTarget || pendingTargets.has(toggledTarget)) {
        return;
      }

      const selected = nextSelected.has(toggledTarget);
      const toggledOption = options.find((option) => option.value === toggledTarget);
      if (!toggledOption) {
        return;
      }

      setPendingTargets((prev) => new Set(prev).add(toggledTarget));
      setOptions((prev) => updateOptionChecked(prev, toggledTarget, selected));

      void (async () => {
        try {
          const result = await setBookTargetState(provider, bookId, toggledTarget, selected);
          setOptions((prev) => updateOptionChecked(prev, toggledTarget, result.selected));

          if (result.changed) {
            emitBookTargetChange({
              provider,
              bookId,
              target: toggledTarget,
              selected: result.selected,
            });
            // When a status was implicitly deselected, sync other instances
            const deselectedTarget = result.deselectedTarget;
            if (deselectedTarget) {
              setOptions((prev) => updateOptionChecked(prev, deselectedTarget, false));
              emitBookTargetChange({
                provider,
                bookId,
                target: deselectedTarget,
                selected: false,
              });
            }
            const list = stripCountSuffix(toggledOption.label);
            onShowToast?.(
              result.selected ? t('Added to {list}', { list }) : t('Removed from {list}', { list }),
              'success',
            );
          }
        } catch (error) {
          setOptions((prev) => updateOptionChecked(prev, toggledTarget, !selected));
          const message =
            error instanceof Error ? error.message : t('Failed to update Hardcover list');
          onShowToast?.(message, 'error');
        } finally {
          setPendingTargets((prev) => {
            const nextPending = new Set(prev);
            nextPending.delete(toggledTarget);
            return nextPending;
          });
        }
      })();
    },
    [bookId, onShowToast, options, pendingTargets, provider, selectedValues],
  );

  let customTrigger: ((props: { isOpen: boolean; toggle: () => void }) => ReactNode) | undefined;
  if (variant === 'pill') {
    customTrigger = ({ toggle }: { isOpen: boolean; toggle: () => void }) => {
      const count = selectedValues.length;
      return (
        <button
          type="button"
          onClick={toggle}
          className={`inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-600 transition-colors hover:bg-emerald-100 focus:outline-hidden dark:bg-emerald-900/20 dark:text-emerald-400 dark:hover:bg-emerald-900/40`}
        >
          <BookmarkIcon className="h-3 w-3" />
          {count > 0 ? t('Hardcover Lists ({count})', { count }) : t('Hardcover Lists')}
        </button>
      );
    };
  } else if (variant === 'icon') {
    customTrigger = ({ toggle }: { isOpen: boolean; toggle: () => void }) => {
      const count = selectedValues.length;
      let title = t('Hardcover Lists');
      if (count === 1) {
        title = t('On 1 Hardcover list');
      } else if (count > 1) {
        title = t('On {count} Hardcover lists', { count });
      }

      return (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            toggle();
          }}
          className={`flex items-center justify-center rounded-full transition-colors duration-200 focus:outline-hidden ${className ?? 'hover-action p-1.5 text-gray-600 sm:p-2 dark:text-gray-200'}`}
          aria-label={t('Hardcover Lists')}
          title={title}
        >
          <BookmarkIcon className={`h-4 w-4 sm:h-5 sm:w-5 ${count > 0 ? 'fill-current' : ''}`} />
        </button>
      );
    };
  }

  return (
    <DropdownList
      options={dropdownOptions}
      value={selectedValues}
      onChange={handleChange}
      placeholder={isLoading ? t('Loading…') : 'Hardcover'}
      widthClassName={variant !== 'default' ? 'w-auto' : widthClassName}
      buttonClassName={variant !== 'default' ? '' : 'py-1.5 leading-none'}
      panelClassName={variant !== 'default' ? 'w-56' : undefined}
      align={align}
      multiple
      showCheckboxes
      keepOpenOnSelect
      positionStrategy={variant === 'icon' ? 'fixed' : 'absolute'}
      summaryFormatter={(selectedOptions) => renderSummary(selectedOptions)}
      renderTrigger={customTrigger}
      onOpenChange={onOpenChange}
    />
  );
};

import { useState, useRef } from 'react';

import { t } from '../../../i18n';
import type {
  OrderableListFieldConfig,
  OrderableListItem,
  OrderableListOption,
} from '../../../types/settings';
import { ToggleSwitch } from '../../shared';

interface OrderableListFieldProps {
  field: OrderableListFieldConfig;
  value: OrderableListItem[];
  onChange: (value: OrderableListItem[]) => void;
  disabled?: boolean;
}

// Represents where the drop indicator should appear
type DropPosition = { index: number; position: 'before' | 'after' } | null;

// Merged item type with all properties
type MergedItem = OrderableListItem & OrderableListOption;

/**
 * Merge current value with options to get full item info.
 * Items in value take precedence; any options not in value are appended.
 */
const mergeValueWithOptions = (
  value: OrderableListItem[],
  options: OrderableListOption[],
): MergedItem[] => {
  const optionsMap = new Map(options.map((opt) => [opt.id, opt]));
  const result: MergedItem[] = [];

  // Add items from value (preserves order)
  for (const item of value) {
    const option = optionsMap.get(item.id);
    if (option) {
      result.push({ ...option, ...item });
      optionsMap.delete(item.id);
    }
  }

  // Add any remaining options not in value
  for (const option of optionsMap.values()) {
    result.push({ ...option, id: option.id, enabled: false });
  }

  return result;
};

export const OrderableListField = ({
  field,
  value,
  onChange,
  disabled,
}: OrderableListFieldProps) => {
  const isDisabled = disabled ?? false;
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
  const [dropPosition, setDropPosition] = useState<DropPosition>(null);
  const dragNodeRef = useRef<HTMLDivElement | null>(null);

  const items = mergeValueWithOptions(value ?? [], field.options);

  // Check if a move is valid (not crossing pinned items)
  const isValidMove = (fromIndex: number): boolean => {
    const fromItem = items[fromIndex];
    if (fromItem?.isPinned) return false;
    return true;
  };

  const handleDragStart = (e: React.DragEvent<HTMLDivElement>, index: number) => {
    const item = items[index];
    if (item?.isPinned) {
      e.preventDefault();
      return;
    }

    setDraggedIndex(index);
    dragNodeRef.current = e.currentTarget;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(index));
    requestAnimationFrame(() => {
      if (dragNodeRef.current) {
        dragNodeRef.current.classList.add('opacity-50');
      }
    });
  };

  const handleDragEnd = () => {
    if (dragNodeRef.current) {
      dragNodeRef.current.classList.remove('opacity-50');
    }
    setDraggedIndex(null);
    setDropPosition(null);
    dragNodeRef.current = null;
  };

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>, index: number) => {
    e.preventDefault();
    if (draggedIndex === null || draggedIndex === index) {
      setDropPosition(null);
      return;
    }

    // Can't drop on pinned items
    if (items[index]?.isPinned) {
      setDropPosition(null);
      return;
    }

    const rect = e.currentTarget.getBoundingClientRect();
    const midpoint = rect.top + rect.height / 2;
    const position = e.clientY < midpoint ? 'before' : 'after';

    setDropPosition({ index, position });
  };

  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    const relatedTarget = e.relatedTarget;
    if (!(relatedTarget instanceof Node) || !e.currentTarget.contains(relatedTarget)) {
      setDropPosition(null);
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    if (draggedIndex === null || dropPosition === null) {
      handleDragEnd();
      return;
    }

    let targetIndex = dropPosition.index;
    if (dropPosition.position === 'after') {
      targetIndex += 1;
    }
    if (draggedIndex < targetIndex) {
      targetIndex -= 1;
    }

    if (draggedIndex === targetIndex || !isValidMove(draggedIndex)) {
      handleDragEnd();
      return;
    }

    const newItems = [...items];
    const [removed] = newItems.splice(draggedIndex, 1);
    newItems.splice(targetIndex, 0, removed);

    const newValue: OrderableListItem[] = newItems.map((item) => ({
      id: item.id,
      enabled: item.enabled,
    }));

    onChange(newValue);
    handleDragEnd();
  };

  const toggleItem = (index: number) => {
    if (isDisabled) return;
    const item = items[index];
    if (item.isLocked) return;

    const newValue: OrderableListItem[] = items.map((it, i) => ({
      id: it.id,
      enabled: i === index ? !it.enabled : it.enabled,
    }));

    onChange(newValue);
  };

  const moveItem = (fromIndex: number, direction: 'up' | 'down') => {
    const toIndex = direction === 'up' ? fromIndex - 1 : fromIndex + 1;
    if (toIndex < 0 || toIndex >= items.length) return;
    if (!isValidMove(fromIndex)) return;

    const newItems = [...items];
    [newItems[fromIndex], newItems[toIndex]] = [newItems[toIndex], newItems[fromIndex]];

    const newValue: OrderableListItem[] = newItems.map((item) => ({
      id: item.id,
      enabled: item.enabled,
    }));

    onChange(newValue);
  };

  const canMoveUp = (index: number): boolean => {
    const item = items[index];
    if (item?.isPinned) return false;
    if (index === 0) return false;
    if (items[index - 1]?.isPinned) return false;
    return true;
  };

  const canMoveDown = (index: number): boolean => {
    const item = items[index];
    if (item?.isPinned) return false;
    if (index === items.length - 1) return false;
    return true;
  };

  const getDropGapIndex = (): number | null => {
    if (!dropPosition) return null;
    return dropPosition.position === 'before' ? dropPosition.index : dropPosition.index + 1;
  };

  const dropGapIndex = getDropGapIndex();

  return (
    <div className="flex flex-col gap-1">
      {items.map((item, index) => {
        const isDragging = draggedIndex === index;
        const isItemDisabled = isDisabled || item.isLocked;
        const isPinned = item.isPinned ?? false;
        const showIndicatorBefore = dropGapIndex === index;

        let dragStateClassName = 'cursor-grab';
        if (isDragging) {
          dragStateClassName = 'cursor-grabbing opacity-50';
        } else if (isPinned) {
          dragStateClassName = 'cursor-default';
        }

        let hoverStateClassName = '';
        if (isDisabled) {
          hoverStateClassName = 'opacity-60';
        } else if (!isPinned) {
          hoverStateClassName = 'hover:bg-(--hover-surface)';
        }

        return (
          <div key={item.id} className="relative">
            {/* Drop indicator */}
            {showIndicatorBefore && (
              <div className="absolute -top-1 right-1 left-1 z-10 h-1 -translate-y-1/2 rounded-full bg-sky-500" />
            )}

            <div
              draggable={!isPinned}
              onDragStart={(e) => handleDragStart(e, index)}
              onDragEnd={handleDragEnd}
              onDragOver={(e) => handleDragOver(e, index)}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={`flex items-center gap-3 rounded-lg border border-(--border-muted) p-3 transition-all duration-150 ${dragStateClassName} ${hoverStateClassName}`}
            >
              {/* Reorder Controls - hidden for pinned items */}
              {!isPinned ? (
                <div className="-my-1 flex shrink-0 flex-col">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      moveItem(index, 'up');
                    }}
                    disabled={!canMoveUp(index)}
                    className={`rounded p-1.5 transition-colors sm:p-0.5 ${
                      !canMoveUp(index)
                        ? 'cursor-not-allowed text-gray-300 dark:text-gray-600'
                        : 'text-gray-400 hover:text-gray-600 sm:hover:bg-gray-100 dark:hover:text-gray-300 sm:dark:hover:bg-gray-700'
                    } `}
                    aria-label={t('Move up')}
                  >
                    <svg
                      className="h-5 w-5 sm:h-4 sm:w-4"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      moveItem(index, 'down');
                    }}
                    disabled={!canMoveDown(index)}
                    className={`rounded p-1.5 transition-colors sm:p-0.5 ${
                      !canMoveDown(index)
                        ? 'cursor-not-allowed text-gray-300 dark:text-gray-600'
                        : 'text-gray-400 hover:text-gray-600 sm:hover:bg-gray-100 dark:hover:text-gray-300 sm:dark:hover:bg-gray-700'
                    } `}
                    aria-label={t('Move down')}
                  >
                    <svg
                      className="h-5 w-5 sm:h-4 sm:w-4"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
                </div>
              ) : (
                <div className="w-5 shrink-0 sm:w-4" />
              )}

              {/* Label and Description */}
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">{item.label}</div>
                {item.description && (
                  <div className="mt-0.5 text-xs text-(--text-muted)">{item.description}</div>
                )}
                {item.isLocked && item.disabledReason && (
                  <div className="mt-0.5 flex items-center gap-1 text-xs text-amber-500">
                    <svg className="h-3 w-3" fill="currentColor" viewBox="0 0 20 20">
                      <path
                        fillRule="evenodd"
                        d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z"
                        clipRule="evenodd"
                      />
                    </svg>
                    {item.disabledReason}
                  </div>
                )}
              </div>

              {/* Toggle Switch */}
              <div className="shrink-0">
                <ToggleSwitch
                  checked={item.enabled && !item.isLocked}
                  onChange={() => toggleItem(index)}
                  disabled={isItemDisabled}
                />
              </div>
            </div>
          </div>
        );
      })}
      {/* Drop indicator after last item */}
      {dropGapIndex === items.length && (
        <div className="relative h-0">
          <div className="absolute -top-0.5 right-1 left-1 z-10 h-1 rounded-full bg-sky-500" />
        </div>
      )}
    </div>
  );
};

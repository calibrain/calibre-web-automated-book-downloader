import type { ReactNode } from 'react';
import { useCallback, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { useDismiss } from '../hooks/useDismiss';
import { t } from '../i18n';

const FIXED_DROPDOWN_Z_INDEX = 1050;
const DROPDOWN_GAP_PX = 8;

function getPositioningAncestor(element: HTMLElement | null): HTMLElement | null {
  let current = element?.parentElement;

  while (current) {
    const style = getComputedStyle(current);
    const overflowY = style.overflowY;
    if (overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'hidden') {
      return current;
    }
    current = current.parentElement;
  }

  return null;
}

interface DropdownProps {
  label?: string;
  summary?: ReactNode;
  children: (helpers: { close: () => void }) => ReactNode;
  align?: 'left' | 'right' | 'auto';
  widthClassName?: string;
  buttonClassName?: string;
  panelClassName?: string;
  disabled?: boolean;
  renderTrigger?: (props: { isOpen: boolean; toggle: () => void }) => ReactNode;
  /** Disable max-height and overflow scrolling (for panels with nested dropdowns) */
  noScrollLimit?: boolean;
  triggerChrome?: 'default' | 'minimal';
  positionStrategy?: 'absolute' | 'fixed';
  onOpenChange?: (isOpen: boolean) => void;
}

export const Dropdown = ({
  label,
  summary,
  children,
  align = 'left',
  widthClassName = 'w-full',
  buttonClassName = '',
  panelClassName = '',
  disabled = false,
  renderTrigger,
  noScrollLimit = false,
  triggerChrome = 'default',
  positionStrategy = 'absolute',
  onOpenChange,
}: DropdownProps) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [panelDirection, setPanelDirection] = useState<'down' | 'up'>('down');
  const [resolvedAlign, setResolvedAlign] = useState<'left' | 'right'>(
    align === 'right' ? 'right' : 'left',
  );

  let triggerBorderRadius = '0.5rem';
  if (triggerChrome === 'minimal') {
    triggerBorderRadius = '0';
  } else if (isOpen) {
    triggerBorderRadius = panelDirection === 'down' ? '0.5rem 0.5rem 0 0' : '0 0 0.5rem 0.5rem';
  }

  let panelBorderRadius = '0.5rem';
  if (!renderTrigger) {
    panelBorderRadius = panelDirection === 'down' ? '0 0 0.5rem 0.5rem' : '0.5rem 0.5rem 0 0';
  }

  let panelOffsetClassName = 'top-full -mt-px';
  if (panelDirection === 'down') {
    panelOffsetClassName = renderTrigger ? 'top-full mt-2' : 'top-full -mt-px';
  } else {
    panelOffsetClassName = renderTrigger ? 'bottom-full mb-2' : 'bottom-full -mb-px';
  }

  const toggleOpen = () => {
    if (disabled) return;
    setIsOpen((prev) => {
      const next = !prev;
      onOpenChange?.(next);
      return next;
    });
  };

  const close = useCallback(() => {
    setIsOpen(false);
    onOpenChange?.(false);
  }, [onOpenChange]);

  useDismiss(isOpen, [containerRef, panelRef], close);

  const updatePanelLayout = useCallback(() => {
    if (!triggerRef.current || !panelRef.current) return;

    const rect = triggerRef.current.getBoundingClientRect();
    const panelElement = panelRef.current;
    const boundaryRect =
      positionStrategy === 'absolute'
        ? getPositioningAncestor(containerRef.current)?.getBoundingClientRect()
        : undefined;

    if (positionStrategy === 'fixed' && !panelClassName) {
      panelElement.style.width = `${rect.width}px`;
    } else {
      panelElement.style.removeProperty('width');
    }

    const panelHeight = panelElement.offsetHeight || panelElement.scrollHeight;
    const panelWidth = panelElement.offsetWidth || panelElement.scrollWidth;
    const boundaryTop = boundaryRect?.top ?? 0;
    const boundaryRight = boundaryRect?.right ?? window.innerWidth;
    const boundaryBottom = boundaryRect?.bottom ?? window.innerHeight;
    const boundaryLeft = boundaryRect?.left ?? 0;

    // Direction: flip up if not enough space below but enough above
    const spaceBelow = boundaryBottom - rect.bottom - DROPDOWN_GAP_PX;
    const spaceAbove = rect.top - boundaryTop - DROPDOWN_GAP_PX;
    const shouldOpenUp = spaceBelow < panelHeight && spaceAbove >= panelHeight;
    const nextDirection = shouldOpenUp ? 'up' : 'down';
    setPanelDirection((current) => (current === nextDirection ? current : nextDirection));

    let nextAlign: 'left' | 'right';
    if (align === 'auto') {
      const overflowsRight = rect.left + panelWidth > boundaryRight - DROPDOWN_GAP_PX;
      const overflowsLeft = rect.right - panelWidth < boundaryLeft + DROPDOWN_GAP_PX;
      nextAlign = overflowsRight && !overflowsLeft ? 'right' : 'left';
    } else {
      nextAlign = align === 'right' ? 'right' : 'left';
    }
    setResolvedAlign((current) => (current === nextAlign ? current : nextAlign));

    if (positionStrategy !== 'fixed') {
      panelElement.style.removeProperty('top');
      panelElement.style.removeProperty('left');
      return;
    }

    // Fixed-position panels need explicit viewport coordinates.
    let top: number;
    if (shouldOpenUp) {
      top = renderTrigger ? rect.top - panelHeight - DROPDOWN_GAP_PX : rect.top - panelHeight + 1;
    } else {
      top = renderTrigger ? rect.bottom + DROPDOWN_GAP_PX : rect.bottom - 1;
    }

    let left: number;
    if (nextAlign === 'right') {
      left = rect.right - Math.max(panelWidth, rect.width);
    } else {
      left = rect.left;
    }

    panelElement.style.top = `${top}px`;
    panelElement.style.left = `${left}px`;
  }, [align, panelClassName, positionStrategy, renderTrigger]);

  useLayoutEffect(() => {
    if (!isOpen) {
      return undefined;
    }

    updatePanelLayout();
    window.addEventListener('resize', updatePanelLayout);
    window.addEventListener('scroll', updatePanelLayout, true);

    const resizeObserver =
      typeof ResizeObserver === 'undefined'
        ? null
        : new ResizeObserver(() => {
            updatePanelLayout();
          });
    if (resizeObserver) {
      if (triggerRef.current) {
        resizeObserver.observe(triggerRef.current);
      }
      if (panelRef.current) {
        resizeObserver.observe(panelRef.current);
      }
    }

    return () => {
      resizeObserver?.disconnect();
      window.removeEventListener('resize', updatePanelLayout);
      window.removeEventListener('scroll', updatePanelLayout, true);
    };
  }, [isOpen, updatePanelLayout]);

  const panelChromeClassName = `border ${panelDirection === 'down' ? 'shadow-lg' : ''}`;
  const panelContent = (
    <div className={noScrollLimit ? '' : 'max-h-64 overflow-auto'}>{children({ close })}</div>
  );

  const absolutePanel =
    isOpen && positionStrategy === 'absolute' ? (
      <div
        ref={panelRef}
        className={`absolute ${resolvedAlign === 'right' ? 'right-0' : 'left-0'} ${panelOffsetClassName} z-50 ${panelChromeClassName} ${panelClassName || widthClassName}`}
        style={{
          background: 'var(--bg)',
          borderColor: 'var(--border-muted)',
          borderRadius: panelBorderRadius,
        }}
      >
        {panelContent}
      </div>
    ) : null;

  const fixedPanel =
    isOpen && positionStrategy === 'fixed' && typeof document !== 'undefined'
      ? createPortal(
          <div
            ref={panelRef}
            className={`${panelChromeClassName} ${panelClassName ?? ''}`}
            style={{
              position: 'fixed',
              top: 0,
              left: 0,
              zIndex: FIXED_DROPDOWN_Z_INDEX,
              background: 'var(--bg)',
              borderColor: 'var(--border-muted)',
              borderRadius: panelBorderRadius,
            }}
          >
            {panelContent}
          </div>,
          document.body,
        )
      : null;

  return (
    <div className={widthClassName} ref={containerRef}>
      {label && (
        <label
          htmlFor={dropdownId}
          className="mb-1.5 block cursor-pointer text-xs font-medium text-gray-500 dark:text-gray-400"
        >
          {label}
        </label>
      )}
      <div ref={triggerRef} className="relative">
        {renderTrigger ? (
          renderTrigger({ isOpen, toggle: toggleOpen })
        ) : (
          <button
            id={dropdownId}
            type="button"
            onClick={toggleOpen}
            disabled={disabled}
            className={`flex w-full items-center justify-between gap-2 border px-3 py-2 text-left text-sm focus:outline-hidden focus-visible:ring-0 focus-visible:ring-offset-0 focus-visible:outline-hidden ${triggerChrome !== 'minimal' ? 'dropdown-trigger' : ''} ${buttonClassName}`}
            style={{
              color: 'var(--text)',
              borderColor: triggerChrome === 'minimal' ? 'transparent' : 'var(--border-muted)',
              borderWidth: triggerChrome === 'minimal' ? 0 : undefined,
              borderRadius: triggerBorderRadius,
            }}
          >
            <span className="min-w-0 flex-1 truncate">
              {summary ?? <span className="opacity-60">{t('Select an option')}</span>}
            </span>
            <svg
              className={`h-4 w-4 shrink-0 transition-transform ${isOpen ? 'rotate-180' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
            </svg>
          </button>
        )}
        {absolutePanel}
        {fixedPanel}
      </div>
    </div>
  );
};

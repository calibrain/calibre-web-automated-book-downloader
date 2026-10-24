import { useState } from 'react';

import { t } from '../../../i18n';
import type { ActionButtonConfig, ActionResult } from '../../../types/settings';

interface ActionButtonProps {
  field: ActionButtonConfig;
  onAction: () => Promise<ActionResult>;
  disabled?: boolean;
}

function createDetailEntries(details: string[]): Array<{ key: string; detail: string }> {
  const detailCounts = new Map<string, number>();

  return details.map((detail) => {
    const nextCount = (detailCounts.get(detail) ?? 0) + 1;
    detailCounts.set(detail, nextCount);

    return {
      key: nextCount === 1 ? detail : `${detail}-${nextCount}`,
      detail,
    };
  });
}

export const ActionButton = ({ field, onAction, disabled }: ActionButtonProps) => {
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<ActionResult | null>(null);
  const isDisabled = disabled ?? field.disabled ?? isLoading;

  const handleClick = async () => {
    if (isDisabled) return;
    setIsLoading(true);
    setResult(null);
    try {
      const res = await onAction();
      setResult(res);
    } catch (err) {
      setResult({
        success: false,
        message: err instanceof Error ? err.message : t('Action failed'),
      });
    } finally {
      setIsLoading(false);
    }
  };

  const styleClasses = {
    default: 'bg-(--bg-soft) border border-(--border-muted) hover:bg-(--hover-surface)',
    primary: 'bg-sky-600 text-white hover:bg-sky-700',
    danger: 'bg-red-600 text-white hover:bg-red-700',
  };

  return (
    <div className={`space-y-2 ${field.disabled ? 'opacity-60' : ''}`}>
      <div className="flex items-start gap-3">
        <button
          type="button"
          onClick={() => {
            void handleClick();
          }}
          disabled={isDisabled}
          className={`rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${styleClasses[field.style]}`}
        >
          {isLoading ? (
            <span className="flex items-center gap-2">
              <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24">
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                  fill="none"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                />
              </svg>
              {t('Running...')}
            </span>
          ) : (
            field.label
          )}
        </button>
        {field.description && <span className="pt-2 text-xs opacity-60">{field.description}</span>}
      </div>

      {result && (
        <div
          className={`rounded-lg px-3 py-2 text-sm ${
            result.success
              ? 'bg-green-500/20 text-green-700 dark:text-green-300'
              : 'bg-red-500/20 text-red-700 dark:text-red-300'
          }`}
        >
          <p>{t(result.message)}</p>
          {Array.isArray(result.details) && result.details.length > 0 && (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-xs opacity-90">
              {createDetailEntries(result.details).map(({ key, detail }) => (
                <li key={key}>{t(detail)}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {field.disabled && field.disabledReason && (
        <p className="text-xs text-zinc-500 italic">{field.disabledReason}</p>
      )}
    </div>
  );
};

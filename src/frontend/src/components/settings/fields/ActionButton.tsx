import { useState } from 'react';
import { ActionButtonConfig, ActionResult } from '../../../types/settings';

interface ActionButtonProps {
  field: ActionButtonConfig;
  onAction: () => Promise<ActionResult>;
  disabled?: boolean;
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
        message: err instanceof Error ? err.message : 'Action failed',
      });
    } finally {
      setIsLoading(false);
    }
  };

  const styleClasses = {
    default:
      'bg-[var(--bg-soft)] border border-[var(--border-muted)] hover:bg-[var(--hover-surface)]',
    primary: 'bg-sky-600 text-white hover:bg-sky-700',
    danger: 'bg-red-600 text-white hover:bg-red-700',
  };

  return (
    <div className={`space-y-2 ${field.disabled ? 'opacity-60' : ''}`}>
      <div className="flex items-start gap-3">
        <button
          type="button"
          onClick={handleClick}
          disabled={isDisabled}
          className={`px-4 py-2 rounded-lg text-sm font-medium
                      transition-colors disabled:opacity-60 disabled:cursor-not-allowed
                      ${styleClasses[field.style]}`}
        >
          {isLoading ? (
            <span className="flex items-center gap-2">
              <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
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
              Running...
            </span>
          ) : (
            field.label
          )}
        </button>
        {field.description && (
          <span className="text-xs opacity-60 pt-2">{field.description}</span>
        )}
      </div>

      {result && (
        <div
          className={`text-sm px-3 py-2 rounded-lg ${
            result.success
              ? 'bg-green-500/20 text-green-700 dark:text-green-300'
              : 'bg-red-500/20 text-red-700 dark:text-red-300'
          }`}
        >
          {result.message}
        </div>
      )}

      {field.disabled && field.disabledReason && (
        <p className="text-xs text-zinc-500 italic">{field.disabledReason}</p>
      )}
    </div>
  );
};

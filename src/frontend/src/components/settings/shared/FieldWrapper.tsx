import type { ReactNode } from 'react';

import { t } from '../../../i18n';
import type { SettingsField } from '../../../types/settings';
import { Tooltip } from '../../shared/Tooltip';
import { EnvLockBadge } from './EnvLockBadge';

const MARKDOWN_LINK_PATTERN = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g;

const renderDescriptionWithLinks = (description: string): ReactNode => {
  const parts: ReactNode[] = [];
  let lastIndex = 0;

  MARKDOWN_LINK_PATTERN.lastIndex = 0;
  let match: RegExpExecArray | null = MARKDOWN_LINK_PATTERN.exec(description);
  while (match) {
    const [fullMatch, label, url] = match;
    const matchIndex = match.index;

    if (matchIndex > lastIndex) {
      parts.push(
        <span key={`text-${lastIndex}-${matchIndex}`} className="opacity-60">
          {description.slice(lastIndex, matchIndex)}
        </span>,
      );
    }

    parts.push(
      <a
        key={`${url}-${matchIndex}`}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="text-sky-600 underline dark:text-sky-400"
      >
        {label}
      </a>,
    );

    lastIndex = matchIndex + fullMatch.length;
    match = MARKDOWN_LINK_PATTERN.exec(description);
  }

  if (lastIndex < description.length) {
    parts.push(
      <span key={`text-${lastIndex}-end`} className="opacity-60">
        {description.slice(lastIndex)}
      </span>,
    );
  }

  if (parts.length === 0) {
    return <span className="opacity-60">{description}</span>;
  }

  return parts;
};

interface FieldWrapperProps {
  field: SettingsField;
  children: ReactNode;
  // Optional overrides for dynamic disabled state (from disabledWhen)
  disabledOverride?: boolean;
  disabledReasonOverride?: string;
  resetAction?: {
    label?: string;
    disabled?: boolean;
    onClick: () => void;
  };
  headerRight?: ReactNode;
  userOverrideCount?: number;
  userOverrideDetails?: Array<{
    userId: number;
    username: string;
    value: unknown;
  }>;
}

// Badge shown when a field is disabled
const DisabledBadge = ({ reason }: { reason?: string }) => (
  <span
    className="inline-flex items-center gap-1 rounded border border-zinc-500/30 bg-zinc-500/20 px-1.5 py-0.5 text-xs text-zinc-400"
    title={reason || t('This setting is not available')}
  >
    <svg
      className="h-3 w-3"
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={1.5}
      stroke="currentColor"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"
      />
    </svg>
    {t('Unavailable')}
  </span>
);

// Badge shown when changing a field requires a container restart
const RestartRequiredBadge = () => (
  <span
    className="inline-flex items-center gap-1 rounded border border-amber-500/30 bg-amber-500/20 px-1.5 py-0.5 text-xs text-amber-600 dark:text-amber-400"
    title={t('Changing this setting requires a container restart to take effect')}
  >
    <svg
      className="h-3 w-3"
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={1.5}
      stroke="currentColor"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"
      />
    </svg>
    {t('Restart')}
  </span>
);

function formatUserOverrideValue(value: unknown): string {
  if (value === null || value === undefined) return t('(empty)');
  if (typeof value === 'string') return value || t('(empty)');
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return String(value);
  }
  if (typeof value === 'symbol') {
    return value.description ?? value.toString();
  }
  try {
    return JSON.stringify(value) ?? t('(empty)');
  } catch {
    return t('[unserializable value]');
  }
}

const EMPTY_DETAILS: Array<{ userId: number; username: string; value: unknown }> = [];

const UserOverriddenBadge = ({
  count,
  details = EMPTY_DETAILS,
}: {
  count: number;
  details?: Array<{ userId: number; username: string; value: unknown }>;
}) => {
  const visibleDetails = details.slice(0, 10);
  const extraCount = Math.max(details.length - visibleDetails.length, 0);

  const content = (
    <div className="max-w-xs space-y-1">
      {visibleDetails.map((entry) => (
        <div key={entry.userId} className="text-[11px] leading-snug">
          <span className="font-medium">{entry.username}</span>
          <span className="opacity-70">: {formatUserOverrideValue(entry.value)}</span>
        </div>
      ))}
      {extraCount > 0 && (
        <div className="text-[11px] opacity-70">
          {t('and {count} more...', { count: extraCount })}
        </div>
      )}
    </div>
  );

  return (
    <Tooltip content={content} position="top">
      <span className="inline-flex items-center gap-1 rounded border border-sky-500/30 bg-sky-500/15 px-1.5 py-0.5 text-xs text-sky-500 dark:text-sky-400">
        {count > 1 ? t('User overridden ({count})', { count }) : t('User overridden')}
      </span>
    </Tooltip>
  );
};

const ResetActionButton = ({
  label = t('Reset'),
  disabled = false,
  onClick,
}: {
  label?: string;
  disabled?: boolean;
  onClick: () => void;
}) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    className="shrink-0 text-xs font-medium text-sky-500 transition-colors hover:text-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
  >
    {label}
  </button>
);

export const FieldWrapper = ({
  field,
  children,
  disabledOverride,
  disabledReasonOverride,
  resetAction,
  headerRight,
  userOverrideCount,
  userOverrideDetails,
}: FieldWrapperProps) => {
  // Action buttons and headings handle their own layout
  if (field.type === 'ActionButton' || field.type === 'HeadingField') {
    return children;
  }

  // At this point, field is a regular input field with standard properties
  // Use overrides if provided (from disabledWhen), otherwise use field's static values
  const isDisabled = disabledOverride ?? field.disabled;
  const disabledReason = disabledReasonOverride ?? field.disabledReason;
  const requiresRestart = field.requiresRestart;
  const hasUserOverrides = Boolean(userOverrideCount) && (userOverrideCount || 0) > 0;
  const hasLabel = Boolean(field.label && field.label.trim().length > 0);
  const hasResetAction = Boolean(resetAction);
  const showHeaderLeft =
    hasLabel ||
    field.fromEnv ||
    (requiresRestart && !isDisabled && !field.fromEnv) ||
    (isDisabled && !field.fromEnv) ||
    hasUserOverrides;
  const showHeader = showHeaderLeft || hasResetAction || Boolean(headerRight);

  // ENV-locked fields should only dim the control, not the label/description
  const isFullyDimmed = isDisabled && !field.fromEnv;
  return (
    <div className="space-y-1.5">
      {showHeader && (
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            {hasLabel && (
              <label className={`text-sm font-medium ${isFullyDimmed ? 'text-zinc-500' : ''}`}>
                {field.label}
                {field.required && !isDisabled && <span className="ml-0.5 text-red-500">*</span>}
              </label>
            )}
            {field.fromEnv && <EnvLockBadge />}
            {requiresRestart && !isDisabled && !field.fromEnv && <RestartRequiredBadge />}
            {isDisabled && !field.fromEnv && <DisabledBadge reason={disabledReason} />}
            {hasUserOverrides && (
              <UserOverriddenBadge count={userOverrideCount || 0} details={userOverrideDetails} />
            )}
          </div>
          {(hasResetAction || headerRight) && (
            <div className="flex shrink-0 items-center gap-2">
              {hasResetAction && resetAction && (
                <ResetActionButton
                  label={resetAction.label}
                  disabled={resetAction.disabled}
                  onClick={resetAction.onClick}
                />
              )}
              {headerRight}
            </div>
          )}
        </div>
      )}

      <div className={isFullyDimmed ? 'opacity-50' : ''}>{children}</div>

      {field.description && (
        <p className="text-xs">{renderDescriptionWithLinks(field.description)}</p>
      )}

      {isDisabled && disabledReason && (
        <p className="text-xs text-zinc-400 italic">{disabledReason}</p>
      )}
    </div>
  );
};

import { ReactNode } from 'react';
import { SettingsField } from '../../../types/settings';
import { EnvLockBadge } from './EnvLockBadge';

interface FieldWrapperProps {
  field: SettingsField;
  children: ReactNode;
  // Optional overrides for dynamic disabled state (from disabledWhen)
  disabledOverride?: boolean;
  disabledReasonOverride?: string;
}

// Badge shown when a field is disabled
const DisabledBadge = ({ reason }: { reason?: string }) => (
  <span
    className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded
               bg-zinc-500/20 text-zinc-400 border border-zinc-500/30"
    title={reason || 'This setting is not available'}
  >
    <svg
      className="w-3 h-3"
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
    Unavailable
  </span>
);

// Badge shown when changing a field requires a container restart
const RestartRequiredBadge = () => (
  <span
    className="inline-flex items-center gap-1 px-1.5 py-0.5 text-xs rounded
               bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30"
    title="Changing this setting requires a container restart to take effect"
  >
    <svg
      className="w-3 h-3"
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
    Restart
  </span>
);

export const FieldWrapper = ({
  field,
  children,
  disabledOverride,
  disabledReasonOverride,
}: FieldWrapperProps) => {
  // Action buttons and headings handle their own layout
  if (field.type === 'ActionButton' || field.type === 'HeadingField') {
    return <>{children}</>;
  }

  // At this point, field is a regular input field with standard properties
  // Use overrides if provided (from disabledWhen), otherwise use field's static values
  const isDisabled = disabledOverride ?? field.disabled;
  const disabledReason = disabledReasonOverride ?? field.disabledReason;
  const requiresRestart = field.requiresRestart;

  // ENV-locked fields should only dim the control, not the label/description
  const isFullyDimmed = isDisabled && !field.fromEnv;

  return (
    <div className={`space-y-1.5 ${isFullyDimmed ? 'opacity-60' : ''}`}>
      <div className="flex items-center gap-2">
        <label className={`text-sm font-medium ${isFullyDimmed ? 'text-zinc-500' : ''}`}>
          {field.label}
          {field.required && !isDisabled && <span className="text-red-500 ml-0.5">*</span>}
        </label>
        {field.fromEnv && <EnvLockBadge />}
        {requiresRestart && !isDisabled && !field.fromEnv && <RestartRequiredBadge />}
        {isDisabled && !field.fromEnv && <DisabledBadge reason={disabledReason} />}
      </div>

      {children}

      {field.description && (
        <p className="text-xs opacity-60">{field.description}</p>
      )}

      {isDisabled && disabledReason && (
        <p className="text-xs text-zinc-500 italic">{disabledReason}</p>
      )}
    </div>
  );
};

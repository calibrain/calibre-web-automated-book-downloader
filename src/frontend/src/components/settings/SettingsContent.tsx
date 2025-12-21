import { useEffect, useRef } from 'react';
import {
  SettingsTab,
  SettingsField,
  ActionResult,
  TextFieldConfig,
  PasswordFieldConfig,
  NumberFieldConfig,
  CheckboxFieldConfig,
  SelectFieldConfig,
  MultiSelectFieldConfig,
  ActionButtonConfig,
  HeadingFieldConfig,
} from '../../types/settings';
import { FieldWrapper } from './shared';
import {
  TextField,
  PasswordField,
  NumberField,
  CheckboxField,
  SelectField,
  MultiSelectField,
  ActionButton,
  HeadingField,
} from './fields';

interface SettingsContentProps {
  tab: SettingsTab;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
  onSave: () => Promise<void>;
  onAction: (key: string) => Promise<ActionResult>;
  isSaving: boolean;
  hasChanges: boolean;
}

// Check if a field should be visible based on showWhen condition
function isFieldVisible(
  field: SettingsField,
  values: Record<string, unknown>
): boolean {
  // HeadingField doesn't have showWhen
  if (field.type === 'HeadingField') {
    return true;
  }

  const showWhen = field.showWhen;
  if (!showWhen) return true;

  const currentValue = values[showWhen.field];

  // Handle array of allowed values or single value
  return Array.isArray(showWhen.value)
    ? showWhen.value.includes(currentValue as string)
    : currentValue === showWhen.value;
}

// Check if a field should be disabled based on disabledWhen condition
// Returns { disabled: boolean, reason?: string }
function getDisabledState(
  field: SettingsField,
  values: Record<string, unknown>
): { disabled: boolean; reason?: string } {
  // HeadingField doesn't have disabledWhen
  if (field.type === 'HeadingField') {
    return { disabled: false };
  }

  // Check if value is locked by environment variable
  if ('fromEnv' in field && field.fromEnv) {
    return { disabled: true };
  }

  // Check static disabled first
  if ('disabled' in field && field.disabled) {
    return {
      disabled: true,
      reason: 'disabledReason' in field ? field.disabledReason : undefined,
    };
  }

  // Check disabledWhen condition
  if (!('disabledWhen' in field) || !field.disabledWhen) {
    return { disabled: false };
  }

  const { field: conditionField, value: conditionValue, reason } = field.disabledWhen;
  const currentValue = values[conditionField];

  // Check if condition is met (handles both array and single value)
  const isDisabled = Array.isArray(conditionValue)
    ? conditionValue.includes(currentValue as string)
    : currentValue === conditionValue;

  return {
    disabled: isDisabled,
    reason: isDisabled ? reason : undefined,
  };
}

// Render the appropriate field component based on type
const renderField = (
  field: SettingsField,
  value: unknown,
  onChange: (value: unknown) => void,
  onAction: () => Promise<ActionResult>,
  isDisabled: boolean
) => {
  switch (field.type) {
    case 'TextField':
      return (
        <TextField
          field={field as TextFieldConfig}
          value={(value as string) ?? ''}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'PasswordField':
      return (
        <PasswordField
          field={field as PasswordFieldConfig}
          value={(value as string) ?? ''}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'NumberField':
      return (
        <NumberField
          field={field as NumberFieldConfig}
          value={(value as number) ?? 0}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'CheckboxField':
      return (
        <CheckboxField
          field={field as CheckboxFieldConfig}
          value={(value as boolean) ?? false}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'SelectField':
      return (
        <SelectField
          field={field as SelectFieldConfig}
          value={(value as string) ?? ''}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'MultiSelectField':
      return (
        <MultiSelectField
          field={field as MultiSelectFieldConfig}
          value={(value as string[]) ?? []}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'ActionButton':
      return <ActionButton field={field as ActionButtonConfig} onAction={onAction} disabled={isDisabled} />;
    case 'HeadingField':
      return <HeadingField field={field as HeadingFieldConfig} />;
    default:
      return <div>Unknown field type</div>;
  }
};

export const SettingsContent = ({
  tab,
  values,
  onChange,
  onSave,
  onAction,
  isSaving,
  hasChanges,
}: SettingsContentProps) => {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Reset scroll position when tab changes
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [tab.name]);

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Scrollable content area */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-6"
        style={{ paddingBottom: hasChanges ? 'calc(5rem + env(safe-area-inset-bottom))' : '1.5rem' }}
      >
        <div className="space-y-5">
          {tab.fields
            .filter((field) => isFieldVisible(field, values))
            .map((field) => {
              const disabledState = getDisabledState(field, values);
              return (
                <FieldWrapper
                  key={`${tab.name}-${field.key}`}
                  field={field}
                  disabledOverride={disabledState.disabled}
                  disabledReasonOverride={disabledState.reason}
                >
                  {renderField(
                    field,
                    values[field.key],
                    (v) => onChange(field.key, v),
                    () => onAction(field.key),
                    disabledState.disabled
                  )}
                </FieldWrapper>
              );
            })}
        </div>
      </div>

      {/* Save button - only visible when there are changes */}
      {hasChanges && (
        <div
          className="flex-shrink-0 px-6 py-4 border-t border-[var(--border-muted)] bg-[var(--bg)] animate-slide-up"
          style={{ paddingBottom: 'calc(1rem + env(safe-area-inset-bottom))' }}
        >
          <button
            onClick={onSave}
            disabled={isSaving}
            className="w-full py-2.5 px-4 rounded-lg font-medium transition-colors
                       bg-sky-600 text-white hover:bg-sky-700
                       disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isSaving ? (
              <span className="flex items-center justify-center gap-2">
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
                Saving...
              </span>
            ) : (
              'Save Changes'
            )}
          </button>
        </div>
      )}
    </div>
  );
};

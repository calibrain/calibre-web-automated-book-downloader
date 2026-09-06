import { useState, useCallback, useMemo } from 'react';
import type { ReactNode } from 'react';

import { useBodyScrollLock } from '../hooks/useBodyScrollLock';
import { useEscapeKey } from '../hooks/useEscapeKey';
import { useMountEffect } from '../hooks/useMountEffect';
import type {
  OnboardingFieldGroup,
  OnboardingStep,
  OnboardingStepCondition,
} from '../services/api';
import {
  getOnboarding,
  saveOnboarding,
  skipOnboarding,
  executeSettingsAction,
} from '../services/api';
import type { SettingsField, ActionResult, ShowWhenCondition } from '../types/settings';
import { toBooleanValue, toStringArray, toStringValue } from '../utils/objectHelpers';
import { getOnboardingStepValidationError } from '../utils/onboardingValidation';
import {
  TextField,
  PasswordField,
  NumberField,
  CheckboxField,
  SelectField,
  MultiSelectField,
  TagListField,
  HeadingField,
  ActionButton,
} from './settings/fields';
import { FieldWrapper } from './settings/shared';

interface OnboardingModalProps {
  isOpen: boolean;
  onClose: () => void;
  onComplete: () => void;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

type VisibilityCondition = ShowWhenCondition | OnboardingStepCondition;

function evaluateShowWhenCondition(
  showWhen: VisibilityCondition,
  values: Record<string, unknown>,
): boolean {
  const currentValue = values[showWhen.field];

  if (showWhen.notEmpty) {
    if (Array.isArray(currentValue)) {
      return currentValue.length > 0;
    }
    return currentValue !== undefined && currentValue !== null && currentValue !== '';
  }

  if (Array.isArray(currentValue)) {
    if (Array.isArray(showWhen.value)) {
      return showWhen.value.every((value) => currentValue.includes(value));
    }
    return showWhen.value !== undefined && currentValue.includes(showWhen.value);
  }

  if (Array.isArray(showWhen.value)) {
    const currentStringValue = toStringValue(currentValue);
    return currentStringValue !== undefined && showWhen.value.includes(currentStringValue);
  }

  return currentValue === showWhen.value;
}

// Check if a field should be visible based on showWhen condition
function isFieldVisible(field: SettingsField, values: Record<string, unknown>): boolean {
  if ('hiddenInUi' in field && field.hiddenInUi) {
    return false;
  }

  const showWhen = field.showWhen;
  if (!showWhen) return true;

  if (Array.isArray(showWhen)) {
    return showWhen.every((condition) => evaluateShowWhenCondition(condition, values));
  }

  return evaluateShowWhenCondition(showWhen, values);
}

// Check if a step should be visible based on its showWhen conditions (all must be true)
function isStepVisible(step: OnboardingStep, values: Record<string, unknown>): boolean {
  if (!step.showWhen || step.showWhen.length === 0) return true;

  return step.showWhen.every((condition) => evaluateShowWhenCondition(condition, values));
}

// Render the appropriate field component based on type
const renderField = (
  field: SettingsField,
  value: unknown,
  onChange: (value: unknown) => void,
  onAction: () => Promise<ActionResult>,
  isDisabled: boolean,
) => {
  switch (field.type) {
    case 'TextField':
      return (
        <TextField
          field={field}
          value={toStringValue(value) ?? ''}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'PasswordField':
      return (
        <PasswordField
          field={field}
          value={toStringValue(value) ?? ''}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'NumberField':
      return (
        <NumberField
          field={field}
          value={typeof value === 'number' ? value : field.value}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'CheckboxField':
      return (
        <CheckboxField
          field={field}
          value={toBooleanValue(value) ?? false}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'SelectField':
      return (
        <SelectField
          field={field}
          value={toStringValue(value) ?? ''}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'MultiSelectField':
      return (
        <MultiSelectField
          field={field}
          value={toStringArray(value) ?? []}
          onChange={onChange}
          disabled={isDisabled}
        />
      );
    case 'TagListField':
      return (
        <TagListField
          field={field}
          value={toStringArray(value) ?? []}
          onChange={(v) => onChange(v)}
          disabled={isDisabled}
        />
      );
    case 'ActionButton':
      return <ActionButton field={field} onAction={onAction} disabled={isDisabled} />;
    case 'HeadingField':
      return <HeadingField field={field} />;
    case 'OrderableListField':
    case 'TableField':
    case 'CustomComponentField':
      return <div>Unsupported onboarding field type: {field.type}</div>;
    default:
      return <div>Unknown field type</div>;
  }
};

interface OnboardingFieldGroupPanelProps {
  group: OnboardingFieldGroup;
  children: ReactNode;
}

const OnboardingFieldGroupPanel = ({ group, children }: OnboardingFieldGroupPanelProps) => {
  const [isOpen, setIsOpen] = useState(group.defaultOpen ?? false);

  return (
    <section className="overflow-hidden rounded-lg border border-(--border-muted)">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-4 bg-(--bg-soft) px-4 py-3 text-left transition-colors hover:bg-(--hover-surface)"
        onClick={() => setIsOpen((current) => !current)}
        aria-expanded={isOpen}
        aria-controls={`onboarding-field-group-${group.id}`}
      >
        <span className="min-w-0">
          <span className="block text-sm font-semibold">{group.title}</span>
          {group.description && (
            <span className="mt-0.5 block text-xs opacity-60">{group.description}</span>
          )}
        </span>
        <svg
          className={`h-4 w-4 shrink-0 transition-transform ${isOpen ? 'rotate-180' : ''}`}
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={2}
          stroke="currentColor"
          aria-hidden="true"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
      </button>
      {isOpen && (
        <div id={`onboarding-field-group-${group.id}`} className="space-y-5 px-4 py-4">
          {children}
        </div>
      )}
    </section>
  );
};

export const OnboardingModal = ({
  isOpen,
  onClose,
  onComplete,
  onShowToast,
}: OnboardingModalProps) => {
  const [isClosing, setIsClosing] = useState(false);
  const [sessionVersion, setSessionVersion] = useState(0);

  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
      setSessionVersion((current) => current + 1);
    }, 150);
  }, [onClose]);

  useBodyScrollLock(isOpen);
  useEscapeKey(isOpen, handleClose);

  if (!isOpen && !isClosing) return null;

  return (
    <OnboardingModalSession
      key={sessionVersion}
      isClosing={isClosing}
      handleClose={handleClose}
      onComplete={onComplete}
      onShowToast={onShowToast}
    />
  );
};

interface OnboardingModalSessionProps {
  isClosing: boolean;
  handleClose: () => void;
  onComplete: () => void;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

const OnboardingModalSession = ({
  isClosing,
  handleClose,
  onComplete,
  onShowToast,
}: OnboardingModalSessionProps) => {
  const [steps, setSteps] = useState<OnboardingStep[]>([]);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showStepValidationError, setShowStepValidationError] = useState(false);

  useMountEffect(() => {
    const fetchOnboarding = async () => {
      try {
        setIsLoading(true);
        setError(null);
        const config = await getOnboarding();
        setSteps(config.steps);
        setValues(config.values);
      } catch (err) {
        console.error('Failed to fetch onboarding config:', err);
        setError('Failed to load setup wizard');
      } finally {
        setIsLoading(false);
      }
    };

    void fetchOnboarding();
  });

  // Get visible steps based on current values
  const visibleSteps = useMemo(() => {
    return steps.filter((step) => isStepVisible(step, values));
  }, [steps, values]);

  // Clamp step index to valid range (handles steps becoming hidden)
  const clampedStepIndex =
    visibleSteps.length === 0 ? 0 : Math.min(currentStepIndex, visibleSteps.length - 1);

  // Get current step
  const currentStep = visibleSteps[clampedStepIndex];

  // Get visible fields for current step
  const visibleFields = useMemo(() => {
    if (!currentStep) return [];
    return currentStep.fields.filter((field) => isFieldVisible(field, values));
  }, [currentStep, values]);

  const groupedFieldKeys = useMemo(
    () => new Set(currentStep?.fieldGroups?.flatMap((group) => group.fieldKeys) ?? []),
    [currentStep],
  );

  const ungroupedFields = useMemo(
    () => visibleFields.filter((field) => !groupedFieldKeys.has(field.key)),
    [groupedFieldKeys, visibleFields],
  );

  const stepValidationError = useMemo(
    () => getOnboardingStepValidationError(currentStep, values),
    [currentStep, values],
  );

  // Handle field value changes
  const handleChange = useCallback((key: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [key]: value }));
  }, []);

  // Handle next step
  const handleNext = useCallback(() => {
    if (stepValidationError) {
      setShowStepValidationError(true);
      return;
    }

    setShowStepValidationError(false);
    if (clampedStepIndex < visibleSteps.length - 1) {
      setCurrentStepIndex(clampedStepIndex + 1);
    }
  }, [clampedStepIndex, stepValidationError, visibleSteps.length]);

  // Handle previous step
  const handleBack = useCallback(() => {
    setShowStepValidationError(false);
    if (clampedStepIndex > 0) {
      setCurrentStepIndex(clampedStepIndex - 1);
    }
  }, [clampedStepIndex]);

  // Handle skip
  const handleSkip = useCallback(async () => {
    try {
      setIsSaving(true);
      await skipOnboarding();
      onShowToast?.('Setup skipped - using defaults', 'info');
      handleClose();
      onComplete();
    } catch (err) {
      console.error('Failed to skip onboarding:', err);
      onShowToast?.('Failed to skip setup', 'error');
    } finally {
      setIsSaving(false);
    }
  }, [handleClose, onComplete, onShowToast]);

  // Handle finish (save and complete)
  const handleFinish = useCallback(async () => {
    try {
      setIsSaving(true);
      const result = await saveOnboarding(values);
      if (result.success) {
        onShowToast?.('Setup complete!', 'success');
        handleClose();
        onComplete();
      } else {
        onShowToast?.(result.message || 'Failed to save settings', 'error');
      }
    } catch (err) {
      console.error('Failed to save onboarding:', err);
      onShowToast?.('Failed to save settings', 'error');
    } finally {
      setIsSaving(false);
    }
  }, [values, handleClose, onComplete, onShowToast]);

  // Handle action button (e.g., test connection)
  const handleAction = useCallback(
    async (fieldKey: string): Promise<ActionResult> => {
      if (!currentStep) {
        return { success: false, message: 'No current step' };
      }
      try {
        // Pass current values so actions can use them (e.g., API key for test connection)
        return await executeSettingsAction(currentStep.tab, fieldKey, values);
      } catch (err) {
        return {
          success: false,
          message: err instanceof Error ? err.message : 'Action failed',
        };
      }
    },
    [currentStep, values],
  );

  // Loading state
  if (isLoading) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        <div className="absolute inset-0 bg-black/60" />
        <div className="relative rounded-xl p-8 shadow-2xl" style={{ background: 'var(--bg)' }}>
          <div className="flex items-center gap-3">
            <svg className="h-5 w-5 animate-spin" viewBox="0 0 24 24">
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
            <span>Loading setup wizard...</span>
          </div>
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        <button
          type="button"
          className="absolute inset-0 bg-black/60"
          onClick={handleClose}
          tabIndex={-1}
          aria-label="Close setup wizard"
        />
        <div
          className="relative max-w-md rounded-xl p-8 shadow-2xl"
          style={{ background: 'var(--bg)' }}
        >
          <div className="space-y-4 text-center">
            <div className="text-red-500">
              <svg
                className="mx-auto h-12 w-12"
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth={1.5}
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"
                />
              </svg>
            </div>
            <p className="text-sm">{error}</p>
            <button
              type="button"
              onClick={handleClose}
              className="rounded-lg border border-(--border-muted) bg-(--bg-soft) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--hover-surface)"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    );
  }

  const isFirstStep = clampedStepIndex === 0;
  const isLastStep = clampedStepIndex === visibleSteps.length - 1;
  const progress = ((clampedStepIndex + 1) / visibleSteps.length) * 100;

  const renderWrappedField = (field: SettingsField) => {
    const isDisabled = 'fromEnv' in field ? (field.fromEnv ?? false) : false;
    return (
      <FieldWrapper key={field.key} field={field}>
        {renderField(
          field,
          values[field.key],
          (value) => handleChange(field.key, value),
          () => handleAction(field.key),
          isDisabled,
        )}
      </FieldWrapper>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className={`absolute inset-0 bg-black/60 transition-opacity duration-150 ${isClosing ? 'opacity-0' : 'opacity-100'}`}
      />

      {/* Modal */}
      <div
        className={`relative flex max-h-[min(85vh,750px)] w-full max-w-xl flex-col overflow-hidden rounded-xl border border-(--border-muted) shadow-2xl ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        style={{ background: 'var(--bg)' }}
        role="dialog"
        aria-modal="true"
        aria-label="Setup Wizard"
      >
        <div className="flex-shrink-0">
          {/* Header */}
          <div className="flex items-center justify-between border-b border-(--border-muted) px-6 py-4">
            <div className="flex items-center gap-3">
              <div className="flex h-8 w-8 items-center justify-center rounded-full bg-sky-500/20 text-sm font-medium text-sky-500">
                {clampedStepIndex + 1}
              </div>
              <div>
                <h2 className="text-lg font-semibold">{currentStep?.title || 'Setup'}</h2>
                <p className="text-xs opacity-60">
                  Step {clampedStepIndex + 1} of {visibleSteps.length}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={handleClose}
              className="rounded-lg p-1.5 transition-colors hover:bg-(--hover-surface)"
              aria-label="Close"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth={1.5}
                stroke="currentColor"
                className="h-5 w-5"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          {/* Progress bar */}
          <div className="h-1 bg-(--bg-soft)">
            <div
              className="h-full bg-sky-500 transition-all duration-300"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>

        {/* Content */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="min-h-[280px] space-y-5 px-6 py-5">
            {ungroupedFields.map(renderWrappedField)}
            {currentStep?.fieldGroups?.map((group) => {
              const groupFields = visibleFields.filter((field) =>
                group.fieldKeys.includes(field.key),
              );
              if (groupFields.length === 0) return null;

              return (
                <OnboardingFieldGroupPanel key={group.id} group={group}>
                  {groupFields.map(renderWrappedField)}
                </OnboardingFieldGroupPanel>
              );
            })}
            {showStepValidationError && stepValidationError && (
              <p className="text-sm text-red-600 dark:text-red-400" role="alert">
                {stepValidationError}
              </p>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex h-[68px] flex-shrink-0 items-center justify-between border-t border-(--border-muted) px-6 py-4">
          <div>
            <button
              type="button"
              onClick={() => {
                void handleSkip();
              }}
              disabled={isSaving || !isFirstStep}
              className={`rounded-lg px-4 py-2 text-sm font-medium ${isFirstStep ? 'opacity-60 transition-opacity hover:opacity-100' : 'invisible'}`}
            >
              Skip setup
            </button>
          </div>

          <div className="flex gap-3">
            {!isFirstStep && (
              <button
                type="button"
                onClick={handleBack}
                disabled={isSaving}
                className="rounded-lg border border-(--border-muted) bg-(--bg-soft) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--hover-surface) disabled:cursor-not-allowed disabled:opacity-50"
              >
                Back
              </button>
            )}

            {isLastStep ? (
              <button
                type="button"
                onClick={() => {
                  void handleFinish();
                }}
                disabled={isSaving}
                className="flex items-center gap-2 rounded-lg bg-sky-600 px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isSaving ? (
                  <>
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
                    Saving...
                  </>
                ) : (
                  'Finish Setup'
                )}
              </button>
            ) : (
              <button
                type="button"
                onClick={handleNext}
                disabled={isSaving}
                className="flex items-center gap-1 rounded-lg bg-sky-600 px-5 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Next
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={2}
                  stroke="currentColor"
                  className="h-4 w-4"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M8.25 4.5l7.5 7.5-7.5 7.5"
                  />
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

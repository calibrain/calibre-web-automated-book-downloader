import type { OnboardingStep } from '../services/api';

const DIRECT_DOWNLOAD_SETUP_STEP_IDS = new Set([
  'direct_download_setup',
  'direct_download_setup_direct_mode',
]);

function hasMirrorUrl(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.some((item) => typeof item === 'string' && item.trim().length > 0);
  }

  return typeof value === 'string' && value.trim().length > 0;
}

export function getOnboardingStepValidationError(
  step: OnboardingStep | undefined,
  values: Record<string, unknown>,
): string | null {
  if (!step || !DIRECT_DOWNLOAD_SETUP_STEP_IDS.has(step.id)) {
    return null;
  }

  if (hasMirrorUrl(values.AA_MIRROR_URLS) || hasMirrorUrl(values.OCEANOFPDF_MIRROR_URLS)) {
    return null;
  }

  return 'Add at least one URL.';
}

import { describe, expect, it } from 'vitest';

import type { OnboardingStep } from '../services/api';
import { getOnboardingStepValidationError } from '../utils/onboardingValidation';

const makeStep = (id: string): OnboardingStep => ({
  id,
  title: 'Test step',
  tab: 'download_sources',
  fields: [],
});

describe('getOnboardingStepValidationError', () => {
  it.each(['direct_download_setup', 'direct_download_setup_direct_mode'])(
    'requires a mirror URL for %s',
    (stepId) => {
      expect(getOnboardingStepValidationError(makeStep(stepId), {})).toBe('Add at least one URL.');
    },
  );

  it("accepts an Anna's Archive mirror URL", () => {
    expect(
      getOnboardingStepValidationError(makeStep('direct_download_setup'), {
        AA_MIRROR_URLS: ['https://annas-archive.example'],
      }),
    ).toBeNull();
  });

  it('accepts an OceanofPDF mirror URL', () => {
    expect(
      getOnboardingStepValidationError(makeStep('direct_download_setup'), {
        OCEANOFPDF_MIRROR_URLS: ['https://oceanofpdf.example'],
      }),
    ).toBeNull();
  });

  it('does not apply direct-download validation to other steps', () => {
    expect(getOnboardingStepValidationError(makeStep('release_sources'), {})).toBeNull();
  });
});

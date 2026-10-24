import { describe, it, expect } from 'vitest';

import type { ButtonStateInfo } from '../types/index';
import {
  applyDirectPolicyModeToButtonState,
  applyUniversalPolicyModeToButtonState,
  canRequestBookFromReleaseModal,
} from '../utils/requestPolicyUi';

describe('canRequestBookFromReleaseModal', () => {
  const base = {
    isBrowseFulfilMode: false,
    isAdmin: false,
    requestsEnabled: true,
    allowBookRequestWithDownload: false,
  };

  it('is always on for an admin', () => {
    expect(canRequestBookFromReleaseModal({ ...base, isAdmin: true })).toBe(true);
    expect(canRequestBookFromReleaseModal({ ...base, isAdmin: true, requestsEnabled: false })).toBe(
      true,
    );
  });

  it('is off for a user unless the setting allows it', () => {
    expect(canRequestBookFromReleaseModal(base)).toBe(false);
    expect(canRequestBookFromReleaseModal({ ...base, allowBookRequestWithDownload: true })).toBe(
      true,
    );
  });

  it('needs requests to be on for a user', () => {
    expect(
      canRequestBookFromReleaseModal({
        ...base,
        requestsEnabled: false,
        allowBookRequestWithDownload: true,
      }),
    ).toBe(false);
  });

  it('is off while fulfilling a request, even for an admin', () => {
    expect(
      canRequestBookFromReleaseModal({ ...base, isAdmin: true, isBrowseFulfilMode: true }),
    ).toBe(false);
  });
});

describe('requestPolicyUi', () => {
  const baseDownload: ButtonStateInfo = { text: 'Download', state: 'download' };

  it('maps direct mode to request for request_release', () => {
    expect(applyDirectPolicyModeToButtonState(baseDownload, 'request_release')).toEqual({
      text: 'Request',
      state: 'download',
    });
  });

  it('maps direct mode to unavailable for blocked', () => {
    expect(applyDirectPolicyModeToButtonState(baseDownload, 'blocked')).toEqual({
      text: 'Unavailable',
      state: 'blocked',
    });
  });

  it('preserves non-download direct states', () => {
    const queued: ButtonStateInfo = { text: 'Queued', state: 'queued' };
    expect(applyDirectPolicyModeToButtonState(queued, 'request_release')).toBe(queued);
  });

  it('maps universal mode to request only for request_book', () => {
    expect(applyUniversalPolicyModeToButtonState(baseDownload, 'request_book')).toEqual({
      text: 'Request',
      state: 'download',
    });
  });

  it('maps universal mode to get for download and request_release', () => {
    expect(applyUniversalPolicyModeToButtonState(baseDownload, 'download')).toEqual({
      text: 'Get',
      state: 'download',
    });
    expect(applyUniversalPolicyModeToButtonState(baseDownload, 'request_release')).toEqual({
      text: 'Get',
      state: 'download',
    });
  });

  it('maps universal mode to unavailable for blocked and preserves non-download states', () => {
    expect(applyUniversalPolicyModeToButtonState(baseDownload, 'blocked')).toEqual({
      text: 'Unavailable',
      state: 'blocked',
    });

    const complete: ButtonStateInfo = { text: 'Downloaded', state: 'complete' };
    expect(applyUniversalPolicyModeToButtonState(complete, 'request_book')).toBe(complete);
  });
});

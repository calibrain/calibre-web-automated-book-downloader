import type { ButtonStateInfo, RequestPolicyMode } from '../types';

/**
 * Whether the release modal offers a book-level request ("Add to requests"). Admins always get
 * it. Anyone else gets it only when requests are on and the setting lets users who can download
 * also ask for a book without choosing a release.
 */
export const canRequestBookFromReleaseModal = ({
  isBrowseFulfilMode,
  isAdmin,
  requestsEnabled,
  allowBookRequestWithDownload,
}: {
  isBrowseFulfilMode: boolean;
  isAdmin: boolean;
  requestsEnabled: boolean;
  allowBookRequestWithDownload: boolean;
}): boolean => {
  if (isBrowseFulfilMode) return false;
  if (isAdmin) return true;
  return requestsEnabled && allowBookRequestWithDownload;
};

export const applyDirectPolicyModeToButtonState = (
  baseState: ButtonStateInfo,
  mode: RequestPolicyMode,
): ButtonStateInfo => {
  if (baseState.state !== 'download') {
    return baseState;
  }

  if (mode === 'blocked') {
    return { text: 'Unavailable', state: 'blocked' };
  }

  if (mode === 'request_release') {
    return { text: 'Request', state: 'download' };
  }

  return baseState;
};

export const applyUniversalPolicyModeToButtonState = (
  baseState: ButtonStateInfo,
  mode: RequestPolicyMode,
): ButtonStateInfo => {
  if (baseState.state !== 'download') {
    return baseState;
  }

  if (mode === 'request_book') {
    return { text: 'Request', state: 'download' };
  }

  if (mode === 'blocked') {
    return { text: 'Unavailable', state: 'blocked' };
  }

  return { ...baseState, text: 'Get' };
};

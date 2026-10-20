import type { ContentType } from '../types';

/**
 * Storage key for the tab this browser last chose.
 *
 * Bumped from `preferred-content-type`, which was written on every page load rather than
 * when the user picked a tab, so its value says nothing about what the user wanted. Reading
 * it would let a default that was never chosen outrank the configured one forever.
 */
export const CONTENT_TYPE_STORAGE_KEY = 'preferred-content-type.v2';

/** Where the current content type came from, which decides what may still override it. */
type ContentTypeSource =
  /** Nothing has chosen a tab, so a configured default may still apply. */
  | 'unset'
  /** This browser stored a choice, or something explicit set one. Defaults do not apply. */
  | 'chosen';

export interface ContentTypePreference {
  contentType: ContentType;
  combinedMode: boolean;
  source: ContentTypeSource;
}

/**
 * Resolve the tab to open on.
 *
 * A value this browser stored wins, because the user picked it. Otherwise the configured
 * default applies, and stays `unset` so it does not get mistaken for a choice: a deep link
 * still overrides it, and changing the setting reaches browsers that never picked a tab.
 */
export const resolveContentTypePreference = (
  stored: string | null,
  serverDefault: string | null | undefined,
): ContentTypePreference => {
  if (stored === 'combined') {
    return { contentType: 'ebook', combinedMode: true, source: 'chosen' };
  }
  if (stored === 'ebook' || stored === 'audiobook') {
    return { contentType: stored, combinedMode: false, source: 'chosen' };
  }
  if (serverDefault === 'audiobook' || serverDefault === 'ebook') {
    return { contentType: serverDefault, combinedMode: false, source: 'unset' };
  }
  return { contentType: 'ebook', combinedMode: false, source: 'unset' };
};

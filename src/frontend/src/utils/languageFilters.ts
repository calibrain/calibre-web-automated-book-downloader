import { Language } from '../types';

export const LANGUAGE_OPTION_DEFAULT = 'default';
export const LANGUAGE_OPTION_ALL = 'all';

export const normalizeLanguageSelection = (selected: string[]): string[] => {
  const sanitized = (selected ?? []).filter(Boolean);

  if (sanitized.length === 0) {
    return [LANGUAGE_OPTION_DEFAULT];
  }

  const unique: string[] = [];
  const seen = new Set<string>();

  for (const value of sanitized) {
    if (!seen.has(value)) {
      unique.push(value);
      seen.add(value);
    }
  }

  if (unique.includes(LANGUAGE_OPTION_ALL)) {
    return [LANGUAGE_OPTION_ALL];
  }

  return unique.length ? unique : [LANGUAGE_OPTION_DEFAULT];
};

export const getLanguageFilterValues = (
  selection: string[],
  supportedLanguages: Language[],
  defaultLanguageCodes: string[] = [],
): string[] | null => {
  if (!selection || selection.length === 0) {
    return null;
  }

  const uniqueSelection = Array.from(new Set(selection.filter(Boolean)));

  if (uniqueSelection.includes(LANGUAGE_OPTION_ALL)) {
    return [LANGUAGE_OPTION_ALL];
  }

  const onlyDefaultSelected =
    uniqueSelection.length === 1 && uniqueSelection[0] === LANGUAGE_OPTION_DEFAULT;
  if (onlyDefaultSelected) {
    return null;
  }

  const supportedCodes = new Set(supportedLanguages.map(lang => lang.code));
  const defaultCodes = defaultLanguageCodes.filter(code => supportedCodes.has(code));
  const resolved = new Set<string>();

  uniqueSelection.forEach(code => {
    if (code === LANGUAGE_OPTION_DEFAULT) {
      defaultCodes.forEach(defaultCode => resolved.add(defaultCode));
      return;
    }

    if (supportedCodes.has(code)) {
      resolved.add(code);
    }
  });

  return resolved.size ? Array.from(resolved) : null;
};

export const formatDefaultLanguageLabel = (
  languageCodes: string[],
  supportedLanguages: Language[],
): string => {
  if (!languageCodes || languageCodes.length === 0) {
    return 'Default (env config)';
  }

  const languageNames = supportedLanguages
    .filter(lang => languageCodes.includes(lang.code))
    .map(lang => lang.language);

  if (languageNames.length === 0) {
    return 'Default (env config)';
  }

  const joined = languageNames.slice(0, 3).join(', ');
  const suffix = languageNames.length > 3 ? '…' : '';
  return `Default (${joined}${suffix})`;
};


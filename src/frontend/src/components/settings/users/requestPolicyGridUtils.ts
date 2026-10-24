import { t } from '../../../i18n';
import type { RequestPolicyMode } from '../../../types';
import type { TableFieldConfig } from '../../../types/settings';
import { isRecord, toNormalizedLowercaseTextValue } from './fieldHelpers';

export type RequestPolicyContentType = 'ebook' | 'audiobook';
export type RequestPolicyMatrixMode = Exclude<RequestPolicyMode, 'request_book'>;

export interface RequestPolicyRuleRow {
  source: string;
  content_type: RequestPolicyContentType;
  mode: RequestPolicyMatrixMode;
}

export interface RequestPolicyDefaultsValue {
  ebook: RequestPolicyMode;
  audiobook: RequestPolicyMode;
}

export interface RequestPolicySourceCapability {
  source: string;
  displayName: string;
  supportedContentTypes: RequestPolicyContentType[];
}

export const REQUEST_POLICY_DEFAULT_OPTIONS: Array<{
  value: RequestPolicyMode;
  label: string;
  description: string;
}> = [
  {
    value: 'download',
    label: t('Download'),
    description: t('Everything can be downloaded directly.'),
  },
  {
    value: 'request_release',
    label: t('Request Release'),
    description: t('Users must request a specific release.'),
  },
  {
    value: 'request_book',
    label: t('Request Book'),
    description: t('Users request a book, admin picks the release.'),
  },
  {
    value: 'blocked',
    label: t('Blocked'),
    description: t('No downloads or requests allowed.'),
  },
];

export const REQUEST_POLICY_MODE_LABELS: Record<RequestPolicyMode, string> = {
  download: t('Download'),
  request_release: t('Request Release'),
  request_book: t('Request Book'),
  blocked: t('Blocked'),
};

const MATRIX_MODES: RequestPolicyMatrixMode[] = ['download', 'request_release', 'blocked'];
const CONTENT_TYPES: RequestPolicyContentType[] = ['ebook', 'audiobook'];
const REQUEST_POLICY_MODES: RequestPolicyMode[] = [
  'download',
  'request_release',
  'request_book',
  'blocked',
];
const MODE_RANK: Record<RequestPolicyMode, number> = {
  download: 0,
  request_release: 1,
  request_book: 2,
  blocked: 3,
};

const normalizeSource = (value: unknown): string => toNormalizedLowercaseTextValue(value);

const normalizeContentType = (value: unknown): RequestPolicyContentType | null => {
  const normalized = toNormalizedLowercaseTextValue(value);
  if (normalized === 'ebook') return 'ebook';
  if (normalized === 'audiobook') return 'audiobook';
  return null;
};

export const normalizeRequestPolicyMode = (value: unknown): RequestPolicyMode | null => {
  const normalized = toNormalizedLowercaseTextValue(value);
  return REQUEST_POLICY_MODES.find((option) => option === normalized) ?? null;
};

export const normalizeRequestPolicyMatrixMode = (
  value: unknown,
): RequestPolicyMatrixMode | null => {
  const normalized = toNormalizedLowercaseTextValue(value);
  return MATRIX_MODES.find((option) => option === normalized) ?? null;
};

const toRuleKey = (source: string, contentType: RequestPolicyContentType) =>
  `${source}::${contentType}`;

const sortRules = (rules: RequestPolicyRuleRow[]): RequestPolicyRuleRow[] =>
  rules.toSorted((a, b) => {
    const sourceCmp = a.source.localeCompare(b.source);
    if (sourceCmp !== 0) return sourceCmp;
    return a.content_type.localeCompare(b.content_type);
  });

export const normalizeRequestPolicyDefaults = (
  raw: Partial<Record<RequestPolicyContentType, unknown>>,
  fallback: RequestPolicyMode = 'download',
): RequestPolicyDefaultsValue => {
  const fallbackMode = normalizeRequestPolicyMode(fallback) || 'download';
  return {
    ebook: normalizeRequestPolicyMode(raw.ebook) || fallbackMode,
    audiobook: normalizeRequestPolicyMode(raw.audiobook) || fallbackMode,
  };
};

export const normalizeRequestPolicyRules = (rawRules: unknown): RequestPolicyRuleRow[] => {
  if (!Array.isArray(rawRules)) {
    return [];
  }

  const byKey = new Map<string, RequestPolicyRuleRow>();
  rawRules.forEach((rawRule) => {
    if (!isRecord(rawRule)) {
      return;
    }
    const source = normalizeSource(rawRule.source);
    const contentType = normalizeContentType(rawRule.content_type);
    const mode = normalizeRequestPolicyMatrixMode(rawRule.mode);
    if (!source || !contentType || !mode) {
      return;
    }
    byKey.set(toRuleKey(source, contentType), {
      source,
      content_type: contentType,
      mode,
    });
  });

  return sortRules(Array.from(byKey.values()));
};

const capPolicyMode = (mode: RequestPolicyMode, ceiling: RequestPolicyMode): RequestPolicyMode => {
  return MODE_RANK[mode] < MODE_RANK[ceiling] ? ceiling : mode;
};

export const isMatrixConfigurable = (defaultMode: RequestPolicyMode): boolean => {
  return defaultMode !== 'blocked';
};

export const getAllowedMatrixModes = (
  defaultMode: RequestPolicyMode,
): RequestPolicyMatrixMode[] => {
  if (!isMatrixConfigurable(defaultMode)) {
    return [];
  }

  return MATRIX_MODES.filter((mode) => MODE_RANK[mode] >= MODE_RANK[defaultMode]);
};

export const mergeRequestPolicyRuleLayers = (
  baseRules: RequestPolicyRuleRow[],
  overrideRules: RequestPolicyRuleRow[],
): RequestPolicyRuleRow[] => {
  const merged = new Map<string, RequestPolicyRuleRow>();
  baseRules.forEach((rule) => {
    merged.set(toRuleKey(rule.source, rule.content_type), rule);
  });
  overrideRules.forEach((rule) => {
    merged.set(toRuleKey(rule.source, rule.content_type), rule);
  });
  return sortRules(Array.from(merged.values()));
};

const findRule = (
  rules: RequestPolicyRuleRow[],
  source: string,
  contentType: RequestPolicyContentType,
): RequestPolicyRuleRow | null => {
  const normalizedSource = normalizeSource(source);
  return (
    rules.find((rule) => rule.source === normalizedSource && rule.content_type === contentType) ||
    null
  );
};

export const getInheritedCellMode = (
  source: string,
  contentType: RequestPolicyContentType,
  defaultModes: RequestPolicyDefaultsValue,
  baseRules: RequestPolicyRuleRow[],
): RequestPolicyMode => {
  const ceiling = defaultModes[contentType];
  const fromRule = findRule(baseRules, source, contentType)?.mode;
  return capPolicyMode(fromRule || ceiling, ceiling);
};

export const getEffectiveCellMode = (
  source: string,
  contentType: RequestPolicyContentType,
  defaultModes: RequestPolicyDefaultsValue,
  baseRules: RequestPolicyRuleRow[],
  explicitRules: RequestPolicyRuleRow[],
): RequestPolicyMode => {
  const ceiling = defaultModes[contentType];
  const explicit = findRule(explicitRules, source, contentType)?.mode;
  if (explicit) {
    return capPolicyMode(explicit, ceiling);
  }
  return getInheritedCellMode(source, contentType, defaultModes, baseRules);
};

export const parseSourceCapabilitiesFromRulesField = (
  rulesField: TableFieldConfig | null | undefined,
  fallbackSources: string[] = [],
): RequestPolicySourceCapability[] => {
  if (!rulesField || !Array.isArray(rulesField.columns)) {
    return fallbackSources.map((source) => ({
      source: normalizeSource(source),
      displayName: source,
      supportedContentTypes: ['ebook', 'audiobook'],
    }));
  }

  const sourceColumn = rulesField.columns.find((column) => column.key === 'source');
  const contentTypeColumn = rulesField.columns.find((column) => column.key === 'content_type');

  const sourceOptions = sourceColumn?.options ?? [];
  const contentTypeOptions = contentTypeColumn?.options ?? [];
  const bySource = new Map<string, RequestPolicySourceCapability>();

  sourceOptions.forEach((option) => {
    const source = normalizeSource(option.value);
    if (!source) {
      return;
    }
    bySource.set(source, {
      source,
      displayName: option.label || source,
      supportedContentTypes: [],
    });
  });

  contentTypeOptions.forEach((option) => {
    const source = normalizeSource(option.childOf);
    const contentType = normalizeContentType(option.value);
    if (!source || !contentType) {
      return;
    }

    const existing = bySource.get(source) || {
      source,
      displayName: source,
      supportedContentTypes: [],
    };

    if (!existing.supportedContentTypes.includes(contentType)) {
      existing.supportedContentTypes.push(contentType);
    }
    bySource.set(source, existing);
  });

  fallbackSources.forEach((sourceValue) => {
    const source = normalizeSource(sourceValue);
    if (!source || bySource.has(source)) {
      return;
    }
    bySource.set(source, {
      source,
      displayName: source,
      supportedContentTypes: ['ebook', 'audiobook'],
    });
  });

  const orderedSources = sourceOptions
    .map((option) => normalizeSource(option.value))
    .filter((source) => source && bySource.has(source));
  const extraSources = Array.from(bySource.keys()).filter(
    (source) => !orderedSources.includes(source),
  );

  return [...orderedSources, ...extraSources].map((source) => {
    const row = bySource.get(source);
    if (!row) {
      return {
        source,
        displayName: source,
        supportedContentTypes: [],
      };
    }
    const supported = CONTENT_TYPES.filter((contentType) =>
      row.supportedContentTypes.includes(contentType),
    );
    return {
      source: row.source,
      displayName: row.displayName,
      supportedContentTypes: supported,
    };
  });
};

const isSourceContentTypeSupported = (
  sourceCapabilities: RequestPolicySourceCapability[],
  source: string,
  contentType: RequestPolicyContentType,
): boolean => {
  const normalizedSource = normalizeSource(source);
  const sourceCapability = sourceCapabilities.find((row) => row.source === normalizedSource);
  return Boolean(sourceCapability?.supportedContentTypes.includes(contentType));
};

export const normalizeExplicitRulesForPersistence = ({
  explicitRules,
  defaultModes,
  sourceCapabilities,
}: {
  explicitRules: RequestPolicyRuleRow[];
  /** @deprecated No longer used — kept for call-site compatibility */
  baseRules?: RequestPolicyRuleRow[];
  defaultModes: RequestPolicyDefaultsValue;
  sourceCapabilities: RequestPolicySourceCapability[];
}): RequestPolicyRuleRow[] => {
  const deduped = normalizeRequestPolicyRules(explicitRules);

  const filtered = deduped.filter((rule) => {
    if (!isSourceContentTypeSupported(sourceCapabilities, rule.source, rule.content_type)) {
      return false;
    }

    const defaultMode = defaultModes[rule.content_type];
    const allowedModes = getAllowedMatrixModes(defaultMode);
    if (!allowedModes.includes(rule.mode)) {
      return false;
    }

    return true;
  });

  return sortRules(filtered);
};

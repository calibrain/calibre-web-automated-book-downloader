import { useMemo } from 'react';

import { t } from '../../../i18n';
import type { RequestPolicyMode } from '../../../types';
import { DropdownList } from '../../DropdownList';
import type {
  RequestPolicyContentType,
  RequestPolicyDefaultsValue,
  RequestPolicyRuleRow,
  RequestPolicySourceCapability,
} from './requestPolicyGridUtils';
import {
  getAllowedMatrixModes,
  getEffectiveCellMode,
  getInheritedCellMode,
  isMatrixConfigurable,
  normalizeExplicitRulesForPersistence,
  normalizeRequestPolicyMatrixMode,
  normalizeRequestPolicyMode,
  normalizeRequestPolicyRules,
  REQUEST_POLICY_DEFAULT_OPTIONS,
  REQUEST_POLICY_MODE_LABELS,
} from './requestPolicyGridUtils';

interface RequestPolicyGridProps {
  defaultModes: RequestPolicyDefaultsValue;
  onDefaultModeChange: (contentType: RequestPolicyContentType, mode: RequestPolicyMode) => void;
  onDefaultModeReset?: (contentType: RequestPolicyContentType) => void;
  defaultModeOverrides?: Partial<Record<RequestPolicyContentType, boolean>>;
  defaultModeDisabled?: Partial<Record<RequestPolicyContentType, boolean>>;
  explicitRules: RequestPolicyRuleRow[];
  baseRules?: RequestPolicyRuleRow[];
  onExplicitRulesChange: (rules: RequestPolicyRuleRow[]) => void;
  sourceCapabilities: RequestPolicySourceCapability[];
  rulesDisabled?: boolean;
  showClearOverrides?: boolean;
  onClearOverrides?: () => void;
  clearOverridesDisabled?: boolean;
}

const CONTENT_TYPES: RequestPolicyContentType[] = ['ebook', 'audiobook'];

const formatSourceLabel = (source: string): string => {
  return source
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
};

const toRuleKey = (source: string, contentType: RequestPolicyContentType) =>
  `${source}::${contentType}`;

const getDropdownValue = (value: string | string[]): string => {
  return Array.isArray(value) ? (value[0] ?? '') : value;
};

const modeDescriptions: Record<RequestPolicyMode, string> = {
  download: t('Users can download directly.'),
  request_release: t('Users pick a release and request it.'),
  request_book: t('Users can request a book, admin picks the release.'),
  blocked: t('Downloads and requests are blocked.'),
};

const EMPTY_BASE_RULES: RequestPolicyRuleRow[] = [];

export const RequestPolicyGrid = ({
  defaultModes,
  onDefaultModeChange,
  onDefaultModeReset,
  defaultModeOverrides,
  defaultModeDisabled,
  explicitRules,
  baseRules = EMPTY_BASE_RULES,
  onExplicitRulesChange,
  sourceCapabilities,
  rulesDisabled = false,
  showClearOverrides = false,
  onClearOverrides,
  clearOverridesDisabled = false,
}: RequestPolicyGridProps) => {
  const normalizedExplicitRules = useMemo(
    () =>
      normalizeExplicitRulesForPersistence({
        explicitRules: normalizeRequestPolicyRules(explicitRules),
        baseRules,
        defaultModes,
        sourceCapabilities,
      }),
    [explicitRules, baseRules, defaultModes, sourceCapabilities],
  );

  const explicitRuleMap = useMemo(() => {
    const map = new Map<string, RequestPolicyRuleRow>();
    normalizedExplicitRules.forEach((rule) => {
      map.set(toRuleKey(rule.source, rule.content_type), rule);
    });
    return map;
  }, [normalizedExplicitRules]);

  const sourceRows = sourceCapabilities.map((sourceCapability) => ({
    ...sourceCapability,
    displayName: sourceCapability.displayName || formatSourceLabel(sourceCapability.source),
  }));

  const hasConfigurableColumn = CONTENT_TYPES.some((contentType) =>
    isMatrixConfigurable(defaultModes[contentType]),
  );

  const updateCellRule = (
    source: string,
    contentType: RequestPolicyContentType,
    nextMode: RequestPolicyMode,
  ) => {
    const inheritedMode = getInheritedCellMode(source, contentType, defaultModes, baseRules);
    const nextExplicitRules = normalizedExplicitRules.filter(
      (rule) => !(rule.source === source && rule.content_type === contentType),
    );

    if (nextMode !== inheritedMode) {
      const matrixMode = normalizeRequestPolicyMatrixMode(nextMode);
      if (!matrixMode) {
        return;
      }
      nextExplicitRules.push({
        source,
        content_type: contentType,
        mode: matrixMode,
      });
    }

    const normalized = normalizeExplicitRulesForPersistence({
      explicitRules: nextExplicitRules,
      baseRules,
      defaultModes,
      sourceCapabilities,
    });
    onExplicitRulesChange(normalized);
  };

  const resetCellRule = (source: string, contentType: RequestPolicyContentType) => {
    const nextRules = normalizedExplicitRules.filter(
      (rule) => !(rule.source === source && rule.content_type === contentType),
    );
    onExplicitRulesChange(nextRules);
  };

  return (
    <div className="space-y-3">
      {showClearOverrides && onClearOverrides && (
        <div className="flex justify-end">
          <button
            type="button"
            onClick={onClearOverrides}
            disabled={clearOverridesDisabled}
            className="rounded-lg border border-(--border-muted) bg-(--bg) px-3 py-1.5 text-xs font-medium transition-colors hover:bg-(--hover-surface) disabled:cursor-not-allowed disabled:opacity-50"
          >
            {t('Clear all overrides')}
          </button>
        </div>
      )}

      <div className="rounded-lg border border-(--border-muted)">
        {/* Header */}
        <div className="hidden gap-3 rounded-t-lg border-b border-(--border-muted) bg-(--bg-soft) px-3 py-2 text-xs font-medium opacity-60 sm:grid sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)]">
          <span>{t('Source')}</span>
          <span>{t('Ebook')}</span>
          <span>{t('Audiobook')}</span>
        </div>

        {/* Default row */}
        <div className="grid grid-cols-1 items-center gap-3 border-b-2 border-(--border-muted) bg-(--bg-soft) px-3 py-2.5 sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)]">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">{t('Default')}</p>
          </div>

          {CONTENT_TYPES.map((contentType) => {
            const mode = defaultModes[contentType];
            const isOverridden = Boolean(defaultModeOverrides?.[contentType]);
            const isDisabled = Boolean(defaultModeDisabled?.[contentType]);

            const mobileLabel = (
              <span className="mr-2 text-xs font-medium opacity-50 sm:hidden">
                {contentType === 'ebook' ? t('Ebook:') : t('Audiobook:')}
              </span>
            );

            return (
              <div key={contentType} className="flex items-center gap-1.5">
                {mobileLabel}
                {isDisabled ? (
                  <div className="w-full cursor-not-allowed rounded-lg border border-(--border-muted) bg-(--bg) px-3 py-2 text-sm opacity-60">
                    {REQUEST_POLICY_MODE_LABELS[mode]}
                  </div>
                ) : (
                  <div
                    className={`min-w-0 flex-1 ${
                      isOverridden ? 'rounded-lg ring-1 ring-sky-500/40' : ''
                    }`}
                  >
                    <DropdownList
                      options={REQUEST_POLICY_DEFAULT_OPTIONS}
                      value={mode}
                      onChange={(value) => {
                        const nextMode = normalizeRequestPolicyMode(getDropdownValue(value));
                        if (!nextMode) {
                          return;
                        }
                        onDefaultModeChange(contentType, nextMode);
                      }}
                      widthClassName="w-full"
                    />
                  </div>
                )}
                {isOverridden && onDefaultModeReset && (
                  <button
                    type="button"
                    onClick={() => onDefaultModeReset(contentType)}
                    disabled={isDisabled}
                    className="shrink-0 text-xs text-sky-500 transition-colors hover:text-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {t('Reset')}
                  </button>
                )}
              </div>
            );
          })}
        </div>

        {/* Source rows */}
        {hasConfigurableColumn ? (
          sourceRows.map((sourceRow, index) => (
            <div
              key={sourceRow.source}
              className={`grid grid-cols-1 items-center gap-3 px-3 py-2.5 sm:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)] ${
                index > 0 ? 'border-t border-(--border-muted)' : ''
              }`}
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{sourceRow.displayName}</p>
              </div>

              {CONTENT_TYPES.map((contentType) => {
                const key = toRuleKey(sourceRow.source, contentType);
                const isSupported = sourceRow.supportedContentTypes.includes(contentType);
                const defaultMode = defaultModes[contentType];
                const isConfigurable = isMatrixConfigurable(defaultMode);
                const effectiveMode = getEffectiveCellMode(
                  sourceRow.source,
                  contentType,
                  defaultModes,
                  baseRules,
                  normalizedExplicitRules,
                );
                const explicitRule = explicitRuleMap.get(key);
                const isOverridden = Boolean(explicitRule);

                const mobileLabel = (
                  <span className="mr-2 text-xs font-medium opacity-50 sm:hidden">
                    {contentType === 'ebook' ? t('Ebook:') : t('Audiobook:')}
                  </span>
                );

                if (!isSupported) {
                  return (
                    <div key={key} className="flex min-h-[36px] items-center justify-center">
                      {mobileLabel}
                      <span className="text-xs opacity-40">{t('Not supported')}</span>
                    </div>
                  );
                }

                if (!isConfigurable) {
                  return (
                    <div key={key} className="flex min-h-[36px] items-center justify-center">
                      {mobileLabel}
                      <span className="text-xs opacity-40">
                        {REQUEST_POLICY_MODE_LABELS[effectiveMode]}
                      </span>
                    </div>
                  );
                }

                const allowedModes = getAllowedMatrixModes(defaultMode);
                // When the effective mode isn't an allowed matrix mode (e.g. request_book
                // as a ceiling default), include it as the first option so the dropdown
                // shows the current state and lets the user switch away from it.
                const effectiveMatrixMode = normalizeRequestPolicyMatrixMode(effectiveMode);
                const effectiveModeOption =
                  !effectiveMatrixMode || !allowedModes.includes(effectiveMatrixMode)
                    ? [
                        {
                          value: effectiveMode,
                          label: REQUEST_POLICY_MODE_LABELS[effectiveMode],
                          description: modeDescriptions[effectiveMode],
                        },
                      ]
                    : [];
                const options = [
                  ...effectiveModeOption,
                  ...allowedModes.map((mode) => ({
                    value: mode,
                    label: REQUEST_POLICY_MODE_LABELS[mode],
                    description: modeDescriptions[mode],
                  })),
                ];

                return (
                  <div key={key} className="flex items-center gap-1.5">
                    {mobileLabel}
                    <div
                      className={`min-w-0 flex-1 ${
                        isOverridden ? 'rounded-lg ring-1 ring-sky-500/40' : ''
                      }`}
                    >
                      {rulesDisabled ? (
                        <div className="w-full cursor-not-allowed rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-2 text-sm opacity-60">
                          {REQUEST_POLICY_MODE_LABELS[effectiveMode]}
                        </div>
                      ) : (
                        <DropdownList
                          options={options}
                          value={effectiveMode}
                          onChange={(value) => {
                            const nextMode = normalizeRequestPolicyMode(getDropdownValue(value));
                            if (!nextMode) {
                              return;
                            }
                            updateCellRule(sourceRow.source, contentType, nextMode);
                          }}
                          widthClassName="w-full"
                        />
                      )}
                    </div>
                    {isOverridden && !rulesDisabled && (
                      <button
                        type="button"
                        onClick={() => resetCellRule(sourceRow.source, contentType)}
                        className="shrink-0 text-xs text-sky-500 transition-colors hover:text-sky-400"
                        aria-label={
                          contentType === 'ebook'
                            ? t('Reset {source} ebook override', { source: sourceRow.displayName })
                            : t('Reset {source} audiobook override', {
                                source: sourceRow.displayName,
                              })
                        }
                      >
                        {t('Reset')}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          ))
        ) : (
          <div className="px-3 py-3">
            <p className="text-xs opacity-60">
              {t(
                'Per-source settings become available when a default is set to Download or Request Release.',
              )}
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

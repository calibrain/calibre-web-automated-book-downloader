import { t } from '../../../i18n';
import type {
  CustomComponentFieldConfig,
  HeadingFieldConfig,
  SelectFieldConfig,
  SettingsTab,
  TableFieldConfig,
} from '../../../types/settings';
import { HeadingField } from '../fields';
import { RequestPolicyGrid } from './RequestPolicyGrid';
import type { RequestPolicyRuleRow } from './requestPolicyGridUtils';
import {
  normalizeRequestPolicyDefaults,
  normalizeRequestPolicyRules,
  normalizeExplicitRulesForPersistence,
  parseSourceCapabilitiesFromRulesField,
} from './requestPolicyGridUtils';
import type { PerUserSettings } from './types';

interface UserRequestPolicyOverridesSectionProps {
  usersTab: SettingsTab;
  globalUsersSettingsValues: Record<string, unknown>;
  isUserOverridable: (key: keyof PerUserSettings) => boolean;
  userSettings: PerUserSettings;
  setUserSettings: (updater: (prev: PerUserSettings) => PerUserSettings) => void;
}

const REQUEST_POLICY_OVERRIDE_KEYS: Array<keyof PerUserSettings> = [
  'REQUESTS_ENABLED',
  'REQUEST_POLICY_DEFAULT_EBOOK',
  'REQUEST_POLICY_DEFAULT_AUDIOBOOK',
  'REQUEST_POLICY_RULES',
  'MAX_PENDING_REQUESTS_PER_USER',
  'REQUESTS_ALLOW_NOTES',
];

const requestPolicyHeading: HeadingFieldConfig = {
  type: 'HeadingField',
  key: 'request_policy_overrides_heading',
  title: t('Requests'),
  description: t(
    'Custom request settings for this user. Reset any to fall back to the global defaults.',
  ),
};

const hasOwnNonNull = (settings: PerUserSettings, key: keyof PerUserSettings): boolean => {
  return (
    Object.prototype.hasOwnProperty.call(settings, key) &&
    settings[key] !== null &&
    settings[key] !== undefined
  );
};

const toBoolean = (value: unknown): boolean => {
  if (typeof value === 'boolean') {
    return value;
  }
  if (typeof value === 'string') {
    return ['1', 'true', 'yes', 'on'].includes(value.trim().toLowerCase());
  }
  return Boolean(value);
};

const toStoredRequestPolicyRule = (rule: RequestPolicyRuleRow): Record<string, unknown> => {
  return {
    source: rule.source,
    content_type: rule.content_type,
    mode: rule.mode,
  };
};

export const UserRequestPolicyOverridesSection = ({
  usersTab,
  globalUsersSettingsValues,
  isUserOverridable,
  userSettings,
  setUserSettings,
}: UserRequestPolicyOverridesSectionProps) => {
  const requestsEnabledOverridePresent = hasOwnNonNull(userSettings, 'REQUESTS_ENABLED');
  const effectiveRequestsEnabled = toBoolean(
    requestsEnabledOverridePresent
      ? userSettings.REQUESTS_ENABLED
      : globalUsersSettingsValues.REQUESTS_ENABLED,
  );
  if (!effectiveRequestsEnabled) {
    return null;
  }

  const requestPolicyEditorField = usersTab.fields.find(
    (field): field is CustomComponentFieldConfig =>
      field.key === 'request_policy_editor' && field.type === 'CustomComponentField',
  );
  const rulesField = requestPolicyEditorField?.boundFields?.find(
    (field): field is TableFieldConfig =>
      field.key === 'REQUEST_POLICY_RULES' && field.type === 'TableField',
  );
  const defaultEbookField = requestPolicyEditorField?.boundFields?.find(
    (field): field is SelectFieldConfig =>
      field.key === 'REQUEST_POLICY_DEFAULT_EBOOK' && field.type === 'SelectField',
  );
  const defaultAudioField = requestPolicyEditorField?.boundFields?.find(
    (field): field is SelectFieldConfig =>
      field.key === 'REQUEST_POLICY_DEFAULT_AUDIOBOOK' && field.type === 'SelectField',
  );

  if (!rulesField) {
    return null;
  }

  const canOverrideDefaults =
    isUserOverridable('REQUEST_POLICY_DEFAULT_EBOOK') &&
    isUserOverridable('REQUEST_POLICY_DEFAULT_AUDIOBOOK');
  const canOverrideRules = isUserOverridable('REQUEST_POLICY_RULES');
  if (!canOverrideDefaults && !canOverrideRules) {
    return null;
  }

  const globalDefaults = normalizeRequestPolicyDefaults({
    ebook: globalUsersSettingsValues.REQUEST_POLICY_DEFAULT_EBOOK,
    audiobook: globalUsersSettingsValues.REQUEST_POLICY_DEFAULT_AUDIOBOOK,
  });
  const globalRules = normalizeRequestPolicyRules(globalUsersSettingsValues.REQUEST_POLICY_RULES);

  const hasUserEbookDefault = hasOwnNonNull(userSettings, 'REQUEST_POLICY_DEFAULT_EBOOK');
  const hasUserAudiobookDefault = hasOwnNonNull(userSettings, 'REQUEST_POLICY_DEFAULT_AUDIOBOOK');
  const explicitUserRules = normalizeRequestPolicyRules(userSettings.REQUEST_POLICY_RULES);

  const effectiveDefaults = normalizeRequestPolicyDefaults({
    ebook: hasUserEbookDefault ? userSettings.REQUEST_POLICY_DEFAULT_EBOOK : globalDefaults.ebook,
    audiobook: hasUserAudiobookDefault
      ? userSettings.REQUEST_POLICY_DEFAULT_AUDIOBOOK
      : globalDefaults.audiobook,
  });

  const sourceCapabilities = parseSourceCapabilitiesFromRulesField(rulesField, [
    ...globalRules.map((row) => row.source),
    ...explicitUserRules.map((row) => row.source),
  ]);

  const setRulesOverride = (
    nextRulesRaw: typeof explicitUserRules,
    nextDefaults = effectiveDefaults,
  ) => {
    const normalized = normalizeExplicitRulesForPersistence({
      explicitRules: nextRulesRaw,
      baseRules: globalRules,
      defaultModes: nextDefaults,
      sourceCapabilities,
    });

    setUserSettings((prev) => {
      const next = { ...prev };
      if (normalized.length === 0) {
        delete next.REQUEST_POLICY_RULES;
      } else {
        next.REQUEST_POLICY_RULES = normalized.map(toStoredRequestPolicyRule);
      }
      return next;
    });
  };

  const hasAnyRequestOverrides = REQUEST_POLICY_OVERRIDE_KEYS.some((key) =>
    hasOwnNonNull(userSettings, key),
  );

  return (
    <div className="space-y-3">
      <HeadingField field={requestPolicyHeading} />

      <RequestPolicyGrid
        defaultModes={effectiveDefaults}
        onDefaultModeChange={(contentType, mode) => {
          const settingKey =
            contentType === 'ebook'
              ? ('REQUEST_POLICY_DEFAULT_EBOOK' as const)
              : ('REQUEST_POLICY_DEFAULT_AUDIOBOOK' as const);
          const globalDefault = globalDefaults[contentType];

          setUserSettings((prev) => {
            const next = { ...prev };
            if (mode === globalDefault) {
              delete next[settingKey];
            } else {
              next[settingKey] = mode;
            }
            return next;
          });

          const nextDefaults = {
            ...effectiveDefaults,
            [contentType]: mode,
          };
          setRulesOverride(explicitUserRules, nextDefaults);
        }}
        onDefaultModeReset={(contentType) => {
          const settingKey =
            contentType === 'ebook'
              ? ('REQUEST_POLICY_DEFAULT_EBOOK' as const)
              : ('REQUEST_POLICY_DEFAULT_AUDIOBOOK' as const);
          setUserSettings((prev) => {
            const next = { ...prev };
            delete next[settingKey];
            return next;
          });

          const nextDefaults = {
            ...effectiveDefaults,
            [contentType]: globalDefaults[contentType],
          };
          setRulesOverride(explicitUserRules, nextDefaults);
        }}
        defaultModeOverrides={{
          ebook: hasUserEbookDefault,
          audiobook: hasUserAudiobookDefault,
        }}
        defaultModeDisabled={{
          ebook:
            !isUserOverridable('REQUEST_POLICY_DEFAULT_EBOOK') ||
            Boolean(defaultEbookField?.fromEnv),
          audiobook:
            !isUserOverridable('REQUEST_POLICY_DEFAULT_AUDIOBOOK') ||
            Boolean(defaultAudioField?.fromEnv),
        }}
        explicitRules={explicitUserRules}
        baseRules={globalRules}
        onExplicitRulesChange={(rules) => setRulesOverride(rules)}
        sourceCapabilities={sourceCapabilities}
        rulesDisabled={!isUserOverridable('REQUEST_POLICY_RULES')}
        showClearOverrides
        clearOverridesDisabled={!hasAnyRequestOverrides}
        onClearOverrides={() => {
          setUserSettings((prev) => {
            const next = { ...prev };
            REQUEST_POLICY_OVERRIDE_KEYS.forEach((key) => {
              delete next[key];
            });
            return next;
          });
        }}
      />
    </div>
  );
};

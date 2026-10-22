import { useMemo } from 'react';

import { t } from '../../../i18n';
import type { SelectFieldConfig, TableFieldConfig } from '../../../types/settings';
import type { RequestPolicyContentType, RequestPolicyMode } from '../users';
import { RequestPolicyGrid } from '../users';
import {
  normalizeExplicitRulesForPersistence,
  normalizeRequestPolicyDefaults,
  normalizeRequestPolicyRules,
  parseSourceCapabilitiesFromRulesField,
} from '../users/requestPolicyGridUtils';
import type { CustomSettingsFieldRendererProps } from './types';

export const RequestPolicyGridField = ({
  field,
  values,
  onChange,
  isDisabled,
}: CustomSettingsFieldRendererProps) => {
  const requestRulesField = useMemo(
    () =>
      field.boundFields?.find(
        (boundField): boundField is TableFieldConfig =>
          boundField.key === 'REQUEST_POLICY_RULES' && boundField.type === 'TableField',
      ),
    [field.boundFields],
  );

  const defaultEbookField = useMemo(
    () =>
      field.boundFields?.find(
        (boundField): boundField is SelectFieldConfig =>
          boundField.key === 'REQUEST_POLICY_DEFAULT_EBOOK' && boundField.type === 'SelectField',
      ),
    [field.boundFields],
  );
  const defaultAudioField = useMemo(
    () =>
      field.boundFields?.find(
        (boundField): boundField is SelectFieldConfig =>
          boundField.key === 'REQUEST_POLICY_DEFAULT_AUDIOBOOK' &&
          boundField.type === 'SelectField',
      ),
    [field.boundFields],
  );

  if (!requestRulesField) {
    return (
      <p className="text-xs opacity-60">
        {t('Request policy schema is unavailable for this tab.')}
      </p>
    );
  }

  const globalRequestDefaults = useMemo(
    () =>
      normalizeRequestPolicyDefaults({
        ebook: values.REQUEST_POLICY_DEFAULT_EBOOK,
        audiobook: values.REQUEST_POLICY_DEFAULT_AUDIOBOOK,
      }),
    [values.REQUEST_POLICY_DEFAULT_EBOOK, values.REQUEST_POLICY_DEFAULT_AUDIOBOOK],
  );

  const explicitGlobalRules = useMemo(
    () => normalizeRequestPolicyRules(values.REQUEST_POLICY_RULES),
    [values.REQUEST_POLICY_RULES],
  );

  const requestSourceCapabilities = useMemo(
    () =>
      parseSourceCapabilitiesFromRulesField(
        requestRulesField,
        explicitGlobalRules.map((row) => row.source),
      ),
    [requestRulesField, explicitGlobalRules],
  );

  const onGlobalDefaultModeChange = (
    contentType: RequestPolicyContentType,
    mode: RequestPolicyMode,
  ) => {
    const key =
      contentType === 'ebook' ? 'REQUEST_POLICY_DEFAULT_EBOOK' : 'REQUEST_POLICY_DEFAULT_AUDIOBOOK';
    const nextDefaultModes = {
      ...globalRequestDefaults,
      [contentType]: mode,
    };
    const normalizedRules = normalizeExplicitRulesForPersistence({
      explicitRules: explicitGlobalRules,
      defaultModes: nextDefaultModes,
      sourceCapabilities: requestSourceCapabilities,
    });
    onChange(key, mode);
    onChange('REQUEST_POLICY_RULES', normalizedRules);
  };

  const onGlobalRulesChange = (
    rules: Array<{
      source: string;
      content_type: 'ebook' | 'audiobook';
      mode: 'download' | 'request_release' | 'blocked';
    }>,
  ) => {
    const normalizedRules = normalizeExplicitRulesForPersistence({
      explicitRules: rules,
      defaultModes: globalRequestDefaults,
      sourceCapabilities: requestSourceCapabilities,
    });
    onChange('REQUEST_POLICY_RULES', normalizedRules);
  };

  return (
    <RequestPolicyGrid
      defaultModes={globalRequestDefaults}
      onDefaultModeChange={onGlobalDefaultModeChange}
      defaultModeDisabled={{
        ebook: isDisabled || Boolean(defaultEbookField?.fromEnv),
        audiobook: isDisabled || Boolean(defaultAudioField?.fromEnv),
      }}
      explicitRules={explicitGlobalRules}
      onExplicitRulesChange={onGlobalRulesChange}
      sourceCapabilities={requestSourceCapabilities}
      rulesDisabled={isDisabled || Boolean(requestRulesField.fromEnv)}
    />
  );
};

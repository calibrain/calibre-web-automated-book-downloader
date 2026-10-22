import { t } from '../../../i18n';
import type { DeliveryPreferencesResponse } from '../../../services/api';
import type {
  HeadingFieldConfig,
  MultiSelectFieldConfig,
  SelectFieldConfig,
} from '../../../types/settings';
import { HeadingField, MultiSelectField, SelectField } from '../fields';
import { FieldWrapper } from '../shared';
import {
  getFieldByKey,
  resolveListOverride,
  toNormalizedLowercaseTextValue,
  toTextValue,
} from './fieldHelpers';
import type { PerUserSettings } from './types';

interface UserSearchPreferencesSectionProps {
  searchPreferences: DeliveryPreferencesResponse | null;
  isUserOverridable: (key: keyof PerUserSettings) => boolean;
  userSettings: PerUserSettings;
  setUserSettings: (updater: (prev: PerUserSettings) => PerUserSettings) => void;
}

type SearchSettingKey =
  | 'SEARCH_MODE'
  | 'BOOK_LANGUAGE'
  | 'METADATA_PROVIDER'
  | 'METADATA_PROVIDER_AUDIOBOOK'
  | 'DEFAULT_RELEASE_SOURCE'
  | 'DEFAULT_RELEASE_SOURCE_AUDIOBOOK';

const fallbackSearchModeField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'SEARCH_MODE',
  label: t('Search Mode'),
  description: t('How you want to search for and download books.'),
  value: 'direct',
  options: [
    { value: 'direct', label: t('Direct') },
    { value: 'universal', label: 'Universal' },
  ],
};

const fallbackMetadataProviderField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'METADATA_PROVIDER',
  label: t('Book Metadata Provider'),
  description: t('Choose which metadata provider to use for book searches.'),
  value: '',
  options: [],
};

const fallbackAudiobookMetadataProviderField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'METADATA_PROVIDER_AUDIOBOOK',
  label: t('Audiobook Metadata Provider'),
  description: t('Metadata provider for audiobook searches. Uses the book provider if not set.'),
  value: '',
  options: [{ value: '', label: t('Use main provider') }],
};

const fallbackDefaultReleaseSourceField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'DEFAULT_RELEASE_SOURCE',
  label: t('Default Book Release Source'),
  description: t('The release source tab to open by default in the release modal for books.'),
  value: 'direct_download',
  options: [],
};

const fallbackDefaultAudiobookReleaseSourceField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'DEFAULT_RELEASE_SOURCE_AUDIOBOOK',
  label: t('Default Audiobook Release Source'),
  description: t(
    'The release source tab to open by default in the release modal for audiobooks. Uses the book release source if not set.',
  ),
  value: '',
  options: [{ value: '', label: t('Use book release source') }],
};

const fallbackBookLanguageField: MultiSelectFieldConfig = {
  type: 'MultiSelectField',
  key: 'BOOK_LANGUAGE',
  label: t('Default Book Languages'),
  description: t('Default language filter for searches.'),
  value: [],
  options: [],
};

const searchHeading: HeadingFieldConfig = {
  type: 'HeadingField',
  key: 'search_preferences_heading',
  title: t('Search Preferences'),
  description: t(
    'Personal search settings for this user. Reset to inherit global defaults from Search Mode.',
  ),
};

const normalizeSearchMode = (value: unknown): 'direct' | 'universal' => {
  const normalized = toNormalizedLowercaseTextValue(value);
  return normalized === 'universal' ? 'universal' : 'direct';
};

const toStringValue = (value: unknown): string => {
  return toTextValue(value);
};

export const UserSearchPreferencesSection = ({
  searchPreferences,
  isUserOverridable,
  userSettings,
  setUserSettings,
}: UserSearchPreferencesSectionProps) => {
  if (!searchPreferences) {
    return null;
  }

  const fields = searchPreferences.fields ?? [];
  const globalValues = searchPreferences.globalValues ?? {};
  const preferenceKeySet = new Set(searchPreferences.keys ?? []);

  const searchModeField = getFieldByKey(fields, 'SEARCH_MODE', fallbackSearchModeField);
  const metadataProviderField = getFieldByKey(
    fields,
    'METADATA_PROVIDER',
    fallbackMetadataProviderField,
  );
  const metadataProviderAudiobookField = getFieldByKey(
    fields,
    'METADATA_PROVIDER_AUDIOBOOK',
    fallbackAudiobookMetadataProviderField,
  );
  const defaultReleaseSourceField = getFieldByKey(
    fields,
    'DEFAULT_RELEASE_SOURCE',
    fallbackDefaultReleaseSourceField,
  );
  const defaultAudiobookReleaseSourceField = getFieldByKey(
    fields,
    'DEFAULT_RELEASE_SOURCE_AUDIOBOOK',
    fallbackDefaultAudiobookReleaseSourceField,
  );
  const bookLanguageField = getFieldByKey(fields, 'BOOK_LANGUAGE', fallbackBookLanguageField);

  const { value: bookLanguageValue, isOverridden: isBookLanguageOverridden } = resolveListOverride(
    userSettings.BOOK_LANGUAGE,
    globalValues.BOOK_LANGUAGE,
    Object.prototype.hasOwnProperty.call(userSettings, 'BOOK_LANGUAGE'),
  );

  const isOverridden = (key: SearchSettingKey): boolean => {
    if (
      !Object.prototype.hasOwnProperty.call(userSettings, key) ||
      userSettings[key] === null ||
      userSettings[key] === undefined
    ) {
      return false;
    }

    return toStringValue(userSettings[key]) !== toStringValue(globalValues[key]);
  };

  const readValue = (key: SearchSettingKey, fallback = ''): string => {
    if (isOverridden(key)) {
      return toStringValue(userSettings[key]);
    }
    if (Object.prototype.hasOwnProperty.call(globalValues, key)) {
      return toStringValue(globalValues[key]);
    }
    return fallback;
  };

  const resetKeys = (keys: SearchSettingKey[]) => {
    setUserSettings((prev) => {
      const next = { ...prev };
      keys.forEach((key) => {
        delete next[key];
      });
      return next;
    });
  };

  const searchModeValue = readValue('SEARCH_MODE', 'universal');
  const effectiveSearchMode = normalizeSearchMode(searchModeValue);
  const metadataProviderValue = readValue('METADATA_PROVIDER');
  const metadataProviderAudiobookValue = readValue('METADATA_PROVIDER_AUDIOBOOK');
  const defaultReleaseSourceValue = readValue('DEFAULT_RELEASE_SOURCE');
  const defaultAudiobookReleaseSourceValue = readValue('DEFAULT_RELEASE_SOURCE_AUDIOBOOK');

  const canOverrideSearchMode =
    isUserOverridable('SEARCH_MODE') && preferenceKeySet.has('SEARCH_MODE');
  const canOverrideMetadataProvider =
    isUserOverridable('METADATA_PROVIDER') && preferenceKeySet.has('METADATA_PROVIDER');
  const canOverrideAudiobookMetadataProvider =
    isUserOverridable('METADATA_PROVIDER_AUDIOBOOK') &&
    preferenceKeySet.has('METADATA_PROVIDER_AUDIOBOOK');
  const canOverrideDefaultReleaseSource =
    isUserOverridable('DEFAULT_RELEASE_SOURCE') && preferenceKeySet.has('DEFAULT_RELEASE_SOURCE');
  const canOverrideDefaultAudiobookReleaseSource =
    isUserOverridable('DEFAULT_RELEASE_SOURCE_AUDIOBOOK') &&
    preferenceKeySet.has('DEFAULT_RELEASE_SOURCE_AUDIOBOOK');
  const canOverrideBookLanguage =
    isUserOverridable('BOOK_LANGUAGE') && preferenceKeySet.has('BOOK_LANGUAGE');

  if (
    !canOverrideSearchMode &&
    !canOverrideBookLanguage &&
    !canOverrideMetadataProvider &&
    !canOverrideAudiobookMetadataProvider &&
    !canOverrideDefaultReleaseSource &&
    !canOverrideDefaultAudiobookReleaseSource
  ) {
    return null;
  }

  return (
    <div className="space-y-4">
      <HeadingField field={searchHeading} />

      {canOverrideSearchMode && (
        <FieldWrapper
          field={searchModeField}
          resetAction={
            isOverridden('SEARCH_MODE')
              ? {
                  disabled: Boolean(searchModeField.fromEnv),
                  onClick: () => resetKeys(['SEARCH_MODE']),
                }
              : undefined
          }
        >
          <SelectField
            field={searchModeField}
            value={searchModeValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, SEARCH_MODE: value }))}
            disabled={Boolean(searchModeField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {canOverrideBookLanguage && (
        <FieldWrapper
          field={bookLanguageField}
          resetAction={
            isBookLanguageOverridden
              ? {
                  disabled: Boolean(bookLanguageField.fromEnv),
                  onClick: () => resetKeys(['BOOK_LANGUAGE']),
                }
              : undefined
          }
        >
          <MultiSelectField
            field={bookLanguageField}
            value={bookLanguageValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, BOOK_LANGUAGE: value }))}
            disabled={Boolean(bookLanguageField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {effectiveSearchMode === 'universal' && canOverrideMetadataProvider && (
        <FieldWrapper
          field={metadataProviderField}
          resetAction={
            isOverridden('METADATA_PROVIDER')
              ? {
                  disabled: Boolean(metadataProviderField.fromEnv),
                  onClick: () => resetKeys(['METADATA_PROVIDER']),
                }
              : undefined
          }
        >
          <SelectField
            field={metadataProviderField}
            value={metadataProviderValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, METADATA_PROVIDER: value }))}
            disabled={Boolean(metadataProviderField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {effectiveSearchMode === 'universal' && canOverrideAudiobookMetadataProvider && (
        <FieldWrapper
          field={metadataProviderAudiobookField}
          resetAction={
            isOverridden('METADATA_PROVIDER_AUDIOBOOK')
              ? {
                  disabled: Boolean(metadataProviderAudiobookField.fromEnv),
                  onClick: () => resetKeys(['METADATA_PROVIDER_AUDIOBOOK']),
                }
              : undefined
          }
        >
          <SelectField
            field={metadataProviderAudiobookField}
            value={metadataProviderAudiobookValue}
            onChange={(value) =>
              setUserSettings((prev) => ({ ...prev, METADATA_PROVIDER_AUDIOBOOK: value }))
            }
            disabled={Boolean(metadataProviderAudiobookField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {effectiveSearchMode === 'universal' && canOverrideDefaultReleaseSource && (
        <FieldWrapper
          field={defaultReleaseSourceField}
          resetAction={
            isOverridden('DEFAULT_RELEASE_SOURCE')
              ? {
                  disabled: Boolean(defaultReleaseSourceField.fromEnv),
                  onClick: () => resetKeys(['DEFAULT_RELEASE_SOURCE']),
                }
              : undefined
          }
        >
          <SelectField
            field={defaultReleaseSourceField}
            value={defaultReleaseSourceValue}
            onChange={(value) =>
              setUserSettings((prev) => ({ ...prev, DEFAULT_RELEASE_SOURCE: value }))
            }
            disabled={Boolean(defaultReleaseSourceField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {effectiveSearchMode === 'universal' && canOverrideDefaultAudiobookReleaseSource && (
        <FieldWrapper
          field={defaultAudiobookReleaseSourceField}
          resetAction={
            isOverridden('DEFAULT_RELEASE_SOURCE_AUDIOBOOK')
              ? {
                  disabled: Boolean(defaultAudiobookReleaseSourceField.fromEnv),
                  onClick: () => resetKeys(['DEFAULT_RELEASE_SOURCE_AUDIOBOOK']),
                }
              : undefined
          }
        >
          <SelectField
            field={defaultAudiobookReleaseSourceField}
            value={defaultAudiobookReleaseSourceValue}
            onChange={(value) =>
              setUserSettings((prev) => ({
                ...prev,
                DEFAULT_RELEASE_SOURCE_AUDIOBOOK: value,
              }))
            }
            disabled={Boolean(defaultAudiobookReleaseSourceField.fromEnv)}
          />
        </FieldWrapper>
      )}
    </div>
  );
};

import { t } from '../../../i18n';
import type { DeliveryPreferencesResponse } from '../../../services/api';
import type {
  HeadingFieldConfig,
  MultiSelectFieldConfig,
  SelectFieldConfig,
  TextFieldConfig,
} from '../../../types/settings';
import { HeadingField, MultiSelectField, SelectField, TextField } from '../fields';
import { FieldWrapper } from '../shared';
import {
  getFieldByKey,
  resolveListOverride,
  toNormalizedLowercaseTextValue,
  toTextValue,
} from './fieldHelpers';
import type { PerUserSettings } from './types';

interface UserOverridesSectionProps {
  deliveryPreferences: DeliveryPreferencesResponse | null;
  isUserOverridable: (key: keyof PerUserSettings) => boolean;
  userSettings: PerUserSettings;
  setUserSettings: (updater: (prev: PerUserSettings) => PerUserSettings) => void;
}

const modeOptions = [
  { value: 'folder', label: t('Folder') },
  { value: 'email', label: t('Email (SMTP)') },
  { value: 'booklore', label: 'Grimmory (API)' },
];

const fallbackOutputModeField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'BOOKS_OUTPUT_MODE',
  label: t('Output Mode'),
  description: t('Choose where completed book files are sent.'),
  value: 'folder',
  options: modeOptions,
};

const fallbackDestinationField: TextFieldConfig = {
  type: 'TextField',
  key: 'DESTINATION',
  label: t('Destination'),
  description: t('Directory where downloaded files are saved.'),
  value: '',
  placeholder: '/books',
};

const fallbackDestinationAudiobookField: TextFieldConfig = {
  type: 'TextField',
  key: 'DESTINATION_AUDIOBOOK',
  label: t('Destination'),
  description: t("Directory for this user's audiobook downloads."),
  value: '',
};

const fallbackBookloreLibraryField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'BOOKLORE_LIBRARY_ID',
  label: t('Library'),
  description: t('Grimmory library to upload into.'),
  value: '',
  options: [],
};

const fallbackBooklorePathField: SelectFieldConfig = {
  type: 'SelectField',
  key: 'BOOKLORE_PATH_ID',
  label: t('Path'),
  description: t('Grimmory library path for uploads.'),
  value: '',
  options: [],
  filterByField: 'BOOKLORE_LIBRARY_ID',
};

const fallbackEmailRecipientField: TextFieldConfig = {
  type: 'TextField',
  key: 'EMAIL_RECIPIENT',
  label: t('Email Recipient'),
  description: t('Email address used for this user in Email output mode.'),
  value: '',
  placeholder: 'reader@example.com',
};

const fallbackBrowserDownloadField: MultiSelectFieldConfig = {
  type: 'MultiSelectField',
  key: 'DOWNLOAD_TO_BROWSER_CONTENT_TYPES',
  label: t('Download to Browser'),
  description: t(
    'Automatically download completed files to this browser for the selected content types.',
  ),
  value: [],
  variant: 'dropdown',
  options: [
    { value: 'book', label: t('Books') },
    { value: 'audiobook', label: t('Audiobooks') },
  ],
};

type DeliverySettingKey = keyof PerUserSettings;

function normalizeMode(value: unknown): 'folder' | 'booklore' | 'email' {
  const mode = toNormalizedLowercaseTextValue(value);
  if (mode === 'booklore' || mode === 'email') {
    return mode;
  }
  return 'folder';
}

function toStringValue(value: unknown): string {
  return toTextValue(value);
}

const deliveryHeading: HeadingFieldConfig = {
  type: 'HeadingField',
  key: 'delivery_preferences_heading',
  title: t('Delivery Preferences'),
  description: t(
    'Editing values here creates per-user settings. Use Reset to inherit global values.',
  ),
};

const booksHeading: HeadingFieldConfig = {
  type: 'HeadingField',
  key: 'delivery_preferences_books_heading',
  title: t('Books'),
  description: t('Output mode and destination behavior for ebooks, comics, and magazines.'),
};

const audiobooksHeading: HeadingFieldConfig = {
  type: 'HeadingField',
  key: 'delivery_preferences_audiobooks_heading',
  title: t('Audiobooks'),
  description: t(
    'Audiobooks always use folder output. Use Reset to inherit the global audiobook destination.',
  ),
};

const BOOK_PREFERENCE_KEYS: DeliverySettingKey[] = [
  'BOOKS_OUTPUT_MODE',
  'DESTINATION',
  'BOOKLORE_LIBRARY_ID',
  'BOOKLORE_PATH_ID',
  'EMAIL_RECIPIENT',
];

const AUDIOBOOK_PREFERENCE_KEYS: DeliverySettingKey[] = ['DESTINATION_AUDIOBOOK'];

export const UserOverridesSection = ({
  deliveryPreferences,
  isUserOverridable,
  userSettings,
  setUserSettings,
}: UserOverridesSectionProps) => {
  const fields = deliveryPreferences?.fields ?? [];
  const globalValues = deliveryPreferences?.globalValues ?? {};
  const preferenceKeys = deliveryPreferences?.keys ?? [];

  const outputModeField = getFieldByKey(fields, 'BOOKS_OUTPUT_MODE', fallbackOutputModeField);
  const destinationField = getFieldByKey(fields, 'DESTINATION', fallbackDestinationField);
  const destinationAudiobookField = getFieldByKey(
    fields,
    'DESTINATION_AUDIOBOOK',
    fallbackDestinationAudiobookField,
  );
  const bookloreLibraryField = getFieldByKey(
    fields,
    'BOOKLORE_LIBRARY_ID',
    fallbackBookloreLibraryField,
  );
  const booklorePathField = getFieldByKey(fields, 'BOOKLORE_PATH_ID', fallbackBooklorePathField);
  const emailRecipientFieldSource = getFieldByKey(
    fields,
    'EMAIL_RECIPIENT',
    fallbackEmailRecipientField,
  );
  const browserDownloadField = getFieldByKey(
    fields,
    'DOWNLOAD_TO_BROWSER_CONTENT_TYPES',
    fallbackBrowserDownloadField,
  );
  const emailRecipientField: TextFieldConfig = {
    ...emailRecipientFieldSource,
    label: t('Email Recipient'),
    description: t('Email address used for this user in Email output mode.'),
  };

  const isOverridden = (key: DeliverySettingKey): boolean => {
    if (
      !Object.prototype.hasOwnProperty.call(userSettings, key) ||
      userSettings[key] === null ||
      userSettings[key] === undefined
    ) {
      return false;
    }

    const userValue = toStringValue(userSettings[key]);
    const globalValue = toStringValue(globalValues[key]);
    return userValue !== globalValue;
  };

  const { value: browserDownloadContentTypes, isOverridden: isBrowserDownloadOverridden } =
    resolveListOverride(
      userSettings.DOWNLOAD_TO_BROWSER_CONTENT_TYPES,
      globalValues.DOWNLOAD_TO_BROWSER_CONTENT_TYPES,
      Object.prototype.hasOwnProperty.call(userSettings, 'DOWNLOAD_TO_BROWSER_CONTENT_TYPES'),
    );

  const resetKeys = (keys: DeliverySettingKey[]) => {
    setUserSettings((prev) => {
      const next = { ...prev };
      keys.forEach((key) => {
        delete next[key];
      });
      return next;
    });
  };

  const resetBookloreLibrary = () => {
    resetKeys(['BOOKLORE_LIBRARY_ID', 'BOOKLORE_PATH_ID']);
  };

  const readValue = (key: DeliverySettingKey, fallback = ''): string => {
    if (isOverridden(key)) {
      return toStringValue(userSettings[key]);
    }
    if (key in globalValues) {
      return toStringValue(globalValues[key]);
    }
    return fallback;
  };

  const outputModeValue = readValue('BOOKS_OUTPUT_MODE', 'folder');
  const effectiveOutputMode = normalizeMode(outputModeValue);

  const destinationValue = readValue('DESTINATION');
  const destinationAudiobookValue = readValue('DESTINATION_AUDIOBOOK');
  const libraryValue = readValue('BOOKLORE_LIBRARY_ID');
  const pathValue = readValue('BOOKLORE_PATH_ID');
  const emailRecipientValue = readValue('EMAIL_RECIPIENT');

  const availableBookPreferenceKeys = BOOK_PREFERENCE_KEYS.filter((key) =>
    preferenceKeys.includes(String(key)),
  );
  const availableAudiobookPreferenceKeys = AUDIOBOOK_PREFERENCE_KEYS.filter((key) =>
    preferenceKeys.includes(String(key)),
  );

  const hasBookDeliveryOverride = availableBookPreferenceKeys.some((key) => isOverridden(key));
  const hasAudiobookDeliveryOverride = availableAudiobookPreferenceKeys.some((key) =>
    isOverridden(key),
  );

  const canOverrideOutputMode = isUserOverridable('BOOKS_OUTPUT_MODE');
  const canOverrideBrowserDownload = isUserOverridable('DOWNLOAD_TO_BROWSER_CONTENT_TYPES');
  const canOverrideDestination = isUserOverridable('DESTINATION');
  const canOverrideAudiobookDestination = isUserOverridable('DESTINATION_AUDIOBOOK');
  const canOverrideBookloreLibrary = isUserOverridable('BOOKLORE_LIBRARY_ID');
  const canOverrideBooklorePath = isUserOverridable('BOOKLORE_PATH_ID');
  const canOverrideEmailRecipient = isUserOverridable('EMAIL_RECIPIENT');

  if (!deliveryPreferences) {
    return null;
  }

  return (
    <div className="space-y-4">
      <HeadingField field={deliveryHeading} />

      {canOverrideBrowserDownload && (
        <FieldWrapper
          field={browserDownloadField}
          resetAction={
            isBrowserDownloadOverridden
              ? {
                  disabled: Boolean(browserDownloadField.fromEnv),
                  onClick: () => resetKeys(['DOWNLOAD_TO_BROWSER_CONTENT_TYPES']),
                }
              : undefined
          }
        >
          <MultiSelectField
            field={browserDownloadField}
            value={browserDownloadContentTypes}
            onChange={(value) =>
              setUserSettings((prev) => ({
                ...prev,
                DOWNLOAD_TO_BROWSER_CONTENT_TYPES: value,
              }))
            }
            disabled={Boolean(browserDownloadField.fromEnv)}
          />
        </FieldWrapper>
      )}

      <HeadingField field={booksHeading} />

      {canOverrideOutputMode && (
        <FieldWrapper
          field={outputModeField}
          resetAction={
            hasBookDeliveryOverride
              ? {
                  label: t('Reset all'),
                  onClick: () => resetKeys(availableBookPreferenceKeys),
                }
              : undefined
          }
        >
          <SelectField
            field={outputModeField}
            value={outputModeValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, BOOKS_OUTPUT_MODE: value }))}
            disabled={Boolean(outputModeField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {effectiveOutputMode === 'folder' && canOverrideDestination && (
        <FieldWrapper
          field={destinationField}
          resetAction={
            isOverridden('DESTINATION')
              ? {
                  disabled: Boolean(destinationField.fromEnv),
                  onClick: () => resetKeys(['DESTINATION']),
                }
              : undefined
          }
        >
          <TextField
            field={destinationField}
            value={destinationValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, DESTINATION: value }))}
            disabled={Boolean(destinationField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {effectiveOutputMode === 'booklore' && canOverrideBookloreLibrary && (
        <FieldWrapper
          field={bookloreLibraryField}
          resetAction={
            isOverridden('BOOKLORE_LIBRARY_ID')
              ? {
                  disabled: Boolean(bookloreLibraryField.fromEnv),
                  onClick: resetBookloreLibrary,
                }
              : undefined
          }
        >
          <SelectField
            field={bookloreLibraryField}
            value={libraryValue}
            onChange={(value) => {
              setUserSettings((prev) => ({
                ...prev,
                BOOKLORE_LIBRARY_ID: value,
                BOOKLORE_PATH_ID: '',
              }));
            }}
            disabled={Boolean(bookloreLibraryField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {effectiveOutputMode === 'booklore' && canOverrideBooklorePath && (
        <FieldWrapper
          field={booklorePathField}
          resetAction={
            isOverridden('BOOKLORE_PATH_ID')
              ? {
                  disabled: Boolean(booklorePathField.fromEnv),
                  onClick: () => resetKeys(['BOOKLORE_PATH_ID']),
                }
              : undefined
          }
        >
          <SelectField
            field={booklorePathField}
            value={pathValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, BOOKLORE_PATH_ID: value }))}
            disabled={Boolean(booklorePathField.fromEnv)}
            filterValue={libraryValue || undefined}
          />
        </FieldWrapper>
      )}

      {effectiveOutputMode === 'email' && canOverrideEmailRecipient && (
        <FieldWrapper
          field={emailRecipientField}
          resetAction={
            isOverridden('EMAIL_RECIPIENT')
              ? {
                  disabled: Boolean(emailRecipientField.fromEnv),
                  onClick: () => resetKeys(['EMAIL_RECIPIENT']),
                }
              : undefined
          }
        >
          <TextField
            field={emailRecipientField}
            value={emailRecipientValue}
            onChange={(value) => setUserSettings((prev) => ({ ...prev, EMAIL_RECIPIENT: value }))}
            disabled={Boolean(emailRecipientField.fromEnv)}
          />
        </FieldWrapper>
      )}

      {canOverrideAudiobookDestination && (
        <>
          <HeadingField field={audiobooksHeading} />
          <FieldWrapper
            field={destinationAudiobookField}
            resetAction={
              hasAudiobookDeliveryOverride
                ? {
                    disabled: Boolean(destinationAudiobookField.fromEnv),
                    onClick: () => resetKeys(availableAudiobookPreferenceKeys),
                  }
                : undefined
            }
          >
            <TextField
              field={destinationAudiobookField}
              value={destinationAudiobookValue}
              onChange={(value) =>
                setUserSettings((prev) => ({ ...prev, DESTINATION_AUDIOBOOK: value }))
              }
              disabled={Boolean(destinationAudiobookField.fromEnv)}
            />
          </FieldWrapper>
        </>
      )}
    </div>
  );
};

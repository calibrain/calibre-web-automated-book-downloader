import { t } from '../../../i18n';
import type { DeliveryPreferencesResponse } from '../../../services/api';
import type {
  ActionButtonConfig,
  ActionResult,
  HeadingFieldConfig,
  TableFieldConfig,
} from '../../../types/settings';
import { ActionButton, HeadingField, TableField } from '../fields';
import { FieldWrapper } from '../shared';
import {
  getFieldByKey,
  isRecord,
  toNormalizedLowercaseTextValue,
  toTrimmedTextValue,
} from './fieldHelpers';
import type { PerUserSettings } from './types';

interface UserNotificationOverridesSectionProps {
  notificationPreferences: DeliveryPreferencesResponse | null;
  isUserOverridable: (key: keyof PerUserSettings) => boolean;
  userSettings: PerUserSettings;
  setUserSettings: (updater: (prev: PerUserSettings) => PerUserSettings) => void;
  onTestNotificationRoutes?: (routes: Array<Record<string, unknown>>) => Promise<ActionResult>;
}

type NotificationSettingKey = 'USER_NOTIFICATION_ROUTES';

const ROUTE_EVENT_ALL = 'all';
const USER_ROUTE_EVENT_OPTIONS = [
  { value: ROUTE_EVENT_ALL, label: t('All') },
  { value: 'request_created', label: t('New request submitted') },
  { value: 'request_fulfilled', label: t('Request approved') },
  { value: 'request_rejected', label: t('Request rejected') },
  { value: 'download_complete', label: t('Download complete') },
  { value: 'download_failed', label: t('Download failed') },
];
const ALLOWED_ROUTE_EVENTS = new Set(USER_ROUTE_EVENT_OPTIONS.map((option) => option.value));
const ROUTE_EVENT_ORDER = new Map(
  USER_ROUTE_EVENT_OPTIONS.map((option, index) => [option.value, index]),
);

const fallbackRoutesField: TableFieldConfig = {
  type: 'TableField',
  key: 'USER_NOTIFICATION_ROUTES',
  label: '',
  description: t(
    'Create one route per URL. Start with All, then add event-specific routes for targeted delivery. Need format examples? [View Apprise URL formats](https://appriseit.com/services/).',
  ),
  value: [{ event: [ROUTE_EVENT_ALL], url: '' }],
  columns: [
    {
      key: 'event',
      label: t('Event'),
      type: 'multiselect',
      options: USER_ROUTE_EVENT_OPTIONS,
      defaultValue: [ROUTE_EVENT_ALL],
      placeholder: t('Select events...'),
    },
    {
      key: 'url',
      label: t('Notification URL'),
      type: 'text',
      placeholder: t('e.g. ntfys://ntfy.sh/username-topic'),
    },
  ],
  addLabel: t('Add Route'),
  emptyMessage: t('No routes configured.'),
};

const notificationHeading: HeadingFieldConfig = {
  type: 'HeadingField',
  key: 'notification_preferences_heading',
  title: t('Notifications'),
  description: t(
    'Personal notification preferences for this user. Reset to inherit global defaults from the Notifications tab.',
  ),
};

const testNotificationActionField: ActionButtonConfig = {
  type: 'ActionButton',
  key: 'test_user_notification',
  label: t('Test Notification'),
  description: t('Send a test notification to the configured personal route URLs.'),
  style: 'primary',
};

const normalizeRouteEvents = (rawEventValue: unknown): string[] => {
  let rawValues: unknown[] = [];
  if (Array.isArray(rawEventValue)) {
    rawValues = rawEventValue;
  } else if (rawEventValue !== undefined && rawEventValue !== null) {
    rawValues = [rawEventValue];
  }

  const deduped = new Set<string>();
  rawValues.forEach((rawEvent) => {
    const event = toNormalizedLowercaseTextValue(rawEvent);
    if (!ALLOWED_ROUTE_EVENTS.has(event)) {
      return;
    }
    deduped.add(event);
  });

  if (deduped.has(ROUTE_EVENT_ALL)) {
    return [ROUTE_EVENT_ALL];
  }

  return Array.from(deduped).toSorted((a, b) => {
    return (
      (ROUTE_EVENT_ORDER.get(a) ?? Number.MAX_SAFE_INTEGER) -
      (ROUTE_EVENT_ORDER.get(b) ?? Number.MAX_SAFE_INTEGER)
    );
  });
};

function normalizeRoutesValue(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) {
    return [{ event: [ROUTE_EVENT_ALL], url: '' }];
  }

  const normalized: Array<Record<string, unknown>> = [];
  const seen = new Set<string>();

  value.forEach((row) => {
    if (!isRecord(row)) {
      return;
    }

    const events = normalizeRouteEvents(row.event);
    if (events.length === 0) {
      return;
    }

    const url = toTrimmedTextValue(row.url);
    const key = `${events.join('|')}::${url}`;
    if (seen.has(key)) {
      return;
    }
    seen.add(key);

    normalized.push({ event: events, url });
  });

  return normalized.length > 0 ? normalized : [{ event: [ROUTE_EVENT_ALL], url: '' }];
}

export const UserNotificationOverridesSection = ({
  notificationPreferences,
  isUserOverridable,
  userSettings,
  setUserSettings,
  onTestNotificationRoutes,
}: UserNotificationOverridesSectionProps) => {
  if (!notificationPreferences) {
    return null;
  }

  const fields = notificationPreferences.fields ?? [];
  const globalValues = notificationPreferences.globalValues ?? {};

  const routesField = getFieldByKey(fields, 'USER_NOTIFICATION_ROUTES', fallbackRoutesField);

  const isOverridden = (key: NotificationSettingKey): boolean => {
    if (
      !Object.prototype.hasOwnProperty.call(userSettings, key) ||
      userSettings[key] === null ||
      userSettings[key] === undefined
    ) {
      return false;
    }

    return (
      JSON.stringify(normalizeRoutesValue(userSettings[key])) !==
      JSON.stringify(normalizeRoutesValue(globalValues[key]))
    );
  };

  const resetKeys = (keys: NotificationSettingKey[]) => {
    setUserSettings((prev) => {
      const next = { ...prev };
      keys.forEach((key) => {
        delete next[key];
      });
      return next;
    });
  };

  const readRoutesValue = (key: NotificationSettingKey): Array<Record<string, unknown>> => {
    if (isOverridden(key)) {
      return normalizeRoutesValue(userSettings[key]);
    }
    if (Object.prototype.hasOwnProperty.call(globalValues, key)) {
      return normalizeRoutesValue(globalValues[key]);
    }
    return normalizeRoutesValue([]);
  };

  const routesValue = readRoutesValue('USER_NOTIFICATION_ROUTES');

  const canOverrideRoutes = isUserOverridable('USER_NOTIFICATION_ROUTES');

  if (!canOverrideRoutes) {
    return null;
  }

  return (
    <div className="space-y-4">
      <HeadingField field={notificationHeading} />

      <FieldWrapper
        field={routesField}
        resetAction={
          isOverridden('USER_NOTIFICATION_ROUTES')
            ? {
                disabled: Boolean(routesField.fromEnv),
                onClick: () => resetKeys(['USER_NOTIFICATION_ROUTES']),
              }
            : undefined
        }
      >
        <TableField
          field={routesField}
          value={routesValue}
          onChange={(value) =>
            setUserSettings((prev) => ({ ...prev, USER_NOTIFICATION_ROUTES: value }))
          }
          disabled={Boolean(routesField.fromEnv)}
        />
      </FieldWrapper>

      {onTestNotificationRoutes && (
        <ActionButton
          field={testNotificationActionField}
          onAction={() => onTestNotificationRoutes(routesValue)}
          disabled={Boolean(routesField.fromEnv)}
        />
      )}
    </div>
  );
};

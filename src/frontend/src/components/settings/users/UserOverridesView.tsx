import { t } from '../../../i18n';
import type { DeliveryPreferencesResponse } from '../../../services/api';
import type { ActionResult, SettingsTab } from '../../../types/settings';
import { SettingsSubpage } from '../shared';
import type { PerUserSettings } from './types';
import { UserOverridesSections } from './UserOverridesSections';

interface UserOverridesViewProps {
  embedded?: boolean;
  hasChanges: boolean;
  onBack: () => void;
  deliveryPreferences: DeliveryPreferencesResponse | null;
  searchPreferences: DeliveryPreferencesResponse | null;
  notificationPreferences: DeliveryPreferencesResponse | null;
  isUserOverridable: (key: keyof PerUserSettings) => boolean;
  userSettings: PerUserSettings;
  setUserSettings: (updater: (prev: PerUserSettings) => PerUserSettings) => void;
  usersTab: SettingsTab;
  globalUsersSettingsValues: Record<string, unknown>;
  onTestNotificationRoutes?: (routes: Array<Record<string, unknown>>) => Promise<ActionResult>;
}

export const UserOverridesView = ({
  embedded = false,
  hasChanges,
  onBack,
  deliveryPreferences,
  searchPreferences,
  notificationPreferences,
  isUserOverridable,
  userSettings,
  setUserSettings,
  usersTab,
  globalUsersSettingsValues,
  onTestNotificationRoutes,
}: UserOverridesViewProps) => {
  const content = (
    <div className="space-y-5">
      <div>
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center gap-2 rounded-lg border border-(--border-muted) bg-(--bg) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--hover-surface)"
        >
          <svg
            className="h-4 w-4"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.5}
            stroke="currentColor"
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" />
          </svg>
          {t('Back to User')}
        </button>
      </div>

      <UserOverridesSections
        scope="admin"
        deliveryPreferences={deliveryPreferences}
        searchPreferences={searchPreferences}
        notificationPreferences={notificationPreferences}
        isUserOverridable={isUserOverridable}
        userSettings={userSettings}
        setUserSettings={setUserSettings}
        usersTab={usersTab}
        globalUsersSettingsValues={globalUsersSettingsValues}
        onTestNotificationRoutes={onTestNotificationRoutes}
      />
    </div>
  );

  if (embedded) {
    return content;
  }

  return <SettingsSubpage hasBottomSaveBar={hasChanges}>{content}</SettingsSubpage>;
};

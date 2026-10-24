import { useCallback, useState } from 'react';

import type { PerUserSettings } from '../components/settings/users/types';
import { t } from '../i18n';
import type { AdminUser, DeliveryPreferencesResponse, DownloadDefaults } from '../services/api';
import {
  getAdminDeliveryPreferences,
  getAdminNotificationPreferences,
  getAdminSearchPreferences,
  getAdminUser,
  getAdminUsers,
  getDownloadDefaults,
  getSettingsTab,
} from '../services/api';
import type { SettingsField } from '../types/settings';
import { useMountEffect } from './useMountEffect';

interface UseUsersFetchParams {
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
}

let cachedUsers: AdminUser[] | null = null;
let cachedLoadError: string | null = null;
let usersCacheLoadPromise: Promise<AdminUser[]> | null = null;

// API errors arrive translated.
const shouldSuppressAccessToast = (message: string): boolean =>
  message.toLowerCase().includes(t('Admin access required').toLowerCase());

const toLoadErrorMessage = (err: unknown): string =>
  err instanceof Error ? err.message : t('Failed to load users');

interface LoadUsersOptions {
  force?: boolean;
}

const loadUsersIntoCache = async ({ force = false }: LoadUsersOptions = {}): Promise<
  AdminUser[]
> => {
  if (!force && cachedUsers !== null) {
    return cachedUsers;
  }
  if (usersCacheLoadPromise) {
    return usersCacheLoadPromise;
  }

  usersCacheLoadPromise = getAdminUsers()
    .then((data) => {
      cachedUsers = data;
      cachedLoadError = null;
      return data;
    })
    .finally(() => {
      usersCacheLoadPromise = null;
    });

  return usersCacheLoadPromise;
};

export const primeUsersCache = async (): Promise<void> => {
  try {
    await loadUsersIntoCache();
  } catch {
    // Silent best-effort warmup.
  }
};

export interface UserEditContext {
  user: AdminUser;
  downloadDefaults: DownloadDefaults;
  deliveryPreferences: DeliveryPreferencesResponse | null;
  searchPreferences: DeliveryPreferencesResponse | null;
  notificationPreferences: DeliveryPreferencesResponse | null;
  userSettings: PerUserSettings;
  userOverridableSettings: Set<string>;
}

const getUserOverridableKeys = (fields: SettingsField[]): string[] => {
  const keys: string[] = [];
  const seen = new Set<string>();

  const collect = (candidateFields: SettingsField[]) => {
    candidateFields.forEach((field) => {
      if (field.type === 'CustomComponentField') {
        if (field.boundFields && field.boundFields.length > 0) {
          collect(field.boundFields);
        }
        return;
      }

      if (field.type === 'HeadingField') {
        return;
      }

      if ((field as { userOverridable?: boolean }).userOverridable && !seen.has(field.key)) {
        seen.add(field.key);
        keys.push(field.key);
      }
    });
  };

  collect(fields);
  return keys;
};

export const useUsersFetch = ({ onShowToast }: UseUsersFetchParams) => {
  const [users, setUsers] = useState<AdminUser[]>(() => cachedUsers ?? []);
  const [loading, setLoading] = useState<boolean>(() => cachedUsers === null);
  const [loadError, setLoadError] = useState<string | null>(() => cachedLoadError);

  const fetchUsers = useCallback(
    async ({ force = false }: LoadUsersOptions = {}): Promise<AdminUser[]> => {
      const hasCachedResult = !force && cachedUsers !== null;
      try {
        if (!hasCachedResult) {
          setLoading(true);
        }
        setLoadError(null);
        const data = await loadUsersIntoCache({ force });
        setUsers(data);
        return data;
      } catch (err) {
        const message = toLoadErrorMessage(err);
        cachedLoadError = message;
        setLoadError(message);
        if (!shouldSuppressAccessToast(message)) {
          onShowToast?.(message, 'error');
        }
        return [];
      } finally {
        setLoading(false);
      }
    },
    [onShowToast],
  );

  useMountEffect(() => {
    void fetchUsers();
  });

  const fetchUserEditContext = useCallback(async (userId: number): Promise<UserEditContext> => {
    const [fullUser, defaults] = await Promise.all([getAdminUser(userId), getDownloadDefaults()]);

    let deliveryPreferences: DeliveryPreferencesResponse | null = null;
    let searchPreferences: DeliveryPreferencesResponse | null = null;
    let notificationPreferences: DeliveryPreferencesResponse | null = null;
    let userSettings = {} as PerUserSettings;
    if (fullUser.settings) {
      userSettings = {
        ...fullUser.settings,
      };
    }
    const userOverridableSettings = new Set<string>();

    const [deliveryResult, searchResult, notificationResult] = await Promise.allSettled([
      getAdminDeliveryPreferences(userId),
      getAdminSearchPreferences(userId),
      getAdminNotificationPreferences(userId),
    ]);

    if (deliveryResult.status === 'fulfilled') {
      deliveryPreferences = deliveryResult.value;
      if (deliveryResult.value.userOverrides) {
        userSettings = {
          ...userSettings,
          ...deliveryResult.value.userOverrides,
        };
      }
      deliveryResult.value.keys.forEach((key) => userOverridableSettings.add(key));
    }

    if (searchResult.status === 'fulfilled') {
      searchPreferences = searchResult.value;
      if (searchResult.value.userOverrides) {
        userSettings = {
          ...userSettings,
          ...searchResult.value.userOverrides,
        };
      }
      searchResult.value.keys.forEach((key) => userOverridableSettings.add(key));
    }

    if (notificationResult.status === 'fulfilled') {
      notificationPreferences = notificationResult.value;
      if (notificationResult.value.userOverrides) {
        userSettings = {
          ...userSettings,
          ...notificationResult.value.userOverrides,
        };
      }
      notificationResult.value.keys.forEach((key) => userOverridableSettings.add(key));
    }

    try {
      const usersTab = await getSettingsTab('users');
      const usersOverridableKeys = getUserOverridableKeys(usersTab.fields);
      usersOverridableKeys.forEach((key) => userOverridableSettings.add(key));
    } catch {
      // Users-tab metadata is best-effort; save still validates server-side.
    }

    return {
      user: fullUser,
      downloadDefaults: defaults,
      deliveryPreferences,
      searchPreferences,
      notificationPreferences,
      userSettings,
      userOverridableSettings,
    };
  }, []);

  return {
    users,
    loading,
    loadError,
    fetchUsers,
    fetchUserEditContext,
  };
};

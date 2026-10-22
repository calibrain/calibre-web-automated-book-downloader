import { useState, useCallback } from 'react';

import { t } from '../i18n';
import { getSettings, updateSettings, executeSettingsAction } from '../services/api';
import type {
  SettingsResponse,
  SettingsTab,
  SettingsGroup,
  ActionResult,
  UpdateResult,
} from '../types/settings';
import {
  cloneSettingsValues,
  extractSettingsValues,
  getRestartRequiredFieldKeys,
  getValueBearingFields,
  mergeFetchedSettingsWithDirtyValues,
  normalizeDependentSelectValues,
  settingsTabMatchesSavedValues,
  type SettingsValues,
} from '../utils/settingsValues';
import {
  getStoredThemePreference,
  setThemePreference,
  THEME_FIELD,
} from '../utils/themePreference';
import { useLatestCallback } from './useLatestCallback';
import { useMountEffect } from './useMountEffect';

interface FetchSettingsOptions {
  silent?: boolean;
  preserveDirtyValues?: boolean;
  force?: boolean;
}

interface UseSettingsReturn {
  tabs: SettingsTab[];
  groups: SettingsGroup[];
  isLoading: boolean;
  error: string | null;
  selectedTab: string | null;
  setSelectedTab: (tab: string | null) => void;
  values: Record<string, Record<string, unknown>>;
  updateValue: (tabName: string, key: string, value: unknown) => void;
  hasChanges: (tabName: string) => boolean;
  saveTab: (tabName: string) => Promise<UpdateResult>;
  executeAction: (tabName: string, actionKey: string) => Promise<ActionResult>;
  isSaving: boolean;
  refetch: () => Promise<void>;
}

interface LoadSettingsOptions {
  force?: boolean;
}

interface HydratedSettingsState {
  tabs: SettingsTab[];
  groups: SettingsGroup[];
  values: SettingsValues;
  originalValues: SettingsValues;
  selectedTab: string | null;
}

let cachedSettingsResponse: SettingsResponse | null = null;
let cachedSettingsLoadError: string | null = null;
let settingsCacheLoadPromise: Promise<SettingsResponse> | null = null;

const hydrateSettingsResponse = (response: SettingsResponse): HydratedSettingsState => {
  const tabs = response.tabs.map((tab) => {
    if (tab.name === 'general') {
      return {
        ...tab,
        fields: [THEME_FIELD, ...tab.fields],
      };
    }
    return tab;
  });

  const values = extractSettingsValues(tabs);
  if (values.general && Object.prototype.hasOwnProperty.call(values.general, THEME_FIELD.key)) {
    values.general[THEME_FIELD.key] = getStoredThemePreference();
  }

  return {
    tabs,
    groups: response.groups || [],
    values,
    originalValues: cloneSettingsValues(values),
    selectedTab: tabs[0]?.name ?? null,
  };
};

const loadSettingsIntoCache = async ({
  force = false,
}: LoadSettingsOptions = {}): Promise<SettingsResponse> => {
  if (!force && cachedSettingsResponse !== null) {
    return cachedSettingsResponse;
  }
  if (settingsCacheLoadPromise) {
    return settingsCacheLoadPromise;
  }

  settingsCacheLoadPromise = getSettings()
    .then((response) => {
      cachedSettingsResponse = response;
      cachedSettingsLoadError = null;
      return response;
    })
    .finally(() => {
      settingsCacheLoadPromise = null;
    });

  return settingsCacheLoadPromise;
};

export const primeSettingsCache = async (): Promise<void> => {
  try {
    await loadSettingsIntoCache();
  } catch {
    // Silent best-effort warmup.
  }
};

export function useSettings(): UseSettingsReturn {
  const initialState =
    cachedSettingsResponse !== null ? hydrateSettingsResponse(cachedSettingsResponse) : null;

  const [tabs, setTabs] = useState<SettingsTab[]>(() => initialState?.tabs ?? []);
  const [groups, setGroups] = useState<SettingsGroup[]>(() => initialState?.groups ?? []);
  const [isLoading, setIsLoading] = useState(() => cachedSettingsResponse === null);
  const [error, setError] = useState<string | null>(() =>
    cachedSettingsResponse === null ? cachedSettingsLoadError : null,
  );
  const [selectedTab, setSelectedTab] = useState<string | null>(
    () => initialState?.selectedTab ?? null,
  );
  const [values, setValues] = useState<SettingsValues>(() => initialState?.values ?? {});
  const [originalValues, setOriginalValues] = useState<SettingsValues>(
    () => initialState?.originalValues ?? {},
  );
  const [isSaving, setIsSaving] = useState(false);
  // Stable identity, latest `values`/`originalValues`. Not an Effect Event: it is called
  // from async fetch and save continuations, not from an Effect. See useLatestCallback.
  const applySettingsResponse = useLatestCallback(
    (response: SettingsResponse, options: { preserveDirtyValues?: boolean } = {}) => {
      const { preserveDirtyValues = false } = options;
      cachedSettingsResponse = response;
      cachedSettingsLoadError = null;

      const hydratedState = hydrateSettingsResponse(response);

      setTabs(hydratedState.tabs);
      setGroups(hydratedState.groups);
      setError(null);

      const nextValues = preserveDirtyValues
        ? mergeFetchedSettingsWithDirtyValues(hydratedState.values, values, originalValues)
        : hydratedState.values;

      setValues(nextValues);
      setOriginalValues(hydratedState.originalValues);

      if (hydratedState.tabs.length > 0) {
        setSelectedTab((current) => current ?? hydratedState.selectedTab);
      }
    },
  );

  const fetchSettings = useCallback(
    async (options: FetchSettingsOptions = {}) => {
      const { silent = false, preserveDirtyValues = false, force = false } = options;
      if (!silent) {
        setIsLoading(true);
        setError(null);
      }
      try {
        const response = await loadSettingsIntoCache({ force });
        applySettingsResponse(response, { preserveDirtyValues });
      } catch (err) {
        const message = err instanceof Error ? err.message : t('Failed to load settings');
        console.error('Failed to fetch settings:', err);
        cachedSettingsLoadError = cachedSettingsResponse === null ? message : null;
        if (!silent) {
          setError(message);
        }
      } finally {
        if (!silent) {
          setIsLoading(false);
        }
      }
    },
    [applySettingsResponse],
  );

  useMountEffect(() => {
    void fetchSettings({
      silent: cachedSettingsResponse !== null,
      force: cachedSettingsResponse !== null,
    });
  });

  const updateValue = useCallback(
    (tabName: string, key: string, value: unknown) => {
      if (key === THEME_FIELD.key && typeof value === 'string') {
        setThemePreference(value);
        setOriginalValues((prev) => ({
          ...prev,
          [tabName]: {
            ...prev[tabName],
            [key]: value,
          },
        }));
      }

      setValues((prev) => {
        const tab = tabs.find((entry) => entry.name === tabName);
        const nextTabValues = {
          ...prev[tabName],
          [key]: value,
        };

        return {
          ...prev,
          [tabName]: tab
            ? normalizeDependentSelectValues(tab.fields, nextTabValues)
            : nextTabValues,
        };
      });
    },
    [tabs],
  );

  const hasChanges = useCallback(
    (tabName: string) => {
      const current = values[tabName];
      const original = originalValues[tabName];
      if (!current || !original) return false;

      const tab = tabs.find((entry) => entry.name === tabName);
      if (!tab) return false;

      for (const field of getValueBearingFields(tab.fields)) {
        const currentValue = current[field.key];
        const originalValue = original[field.key];

        // Compare values - works for all field types including password
        if (JSON.stringify(currentValue) !== JSON.stringify(originalValue)) {
          return true;
        }
      }

      return false;
    },
    [values, originalValues, tabs],
  );

  const saveTab = useCallback(
    async (tabName: string): Promise<UpdateResult> => {
      setIsSaving(true);
      let valuesToSave: Record<string, unknown> = {};
      let restartRequiredFor: string[] = [];
      try {
        const tabValues = values[tabName] || {};
        const originalTabValues = originalValues[tabName] || {};

        // Only send values that actually changed
        const tab = tabs.find((entry) => entry.name === tabName);
        valuesToSave = {};

        if (tab) {
          for (const field of getValueBearingFields(tab.fields)) {
            if (field.fromEnv) continue; // Skip env-locked fields
            if (field.key === THEME_FIELD.key) continue; // Skip client-side only theme field

            const value = tabValues[field.key];
            const originalValue = originalTabValues[field.key];

            // Skip empty password fields
            if (field.type === 'PasswordField' && (!value || value === '')) {
              continue;
            }

            // Only include if value actually changed
            if (JSON.stringify(value) !== JSON.stringify(originalValue)) {
              valuesToSave[field.key] = value;
            }
          }

          restartRequiredFor = getRestartRequiredFieldKeys(tab.fields, valuesToSave);
        }

        const result = await updateSettings(tabName, valuesToSave);

        if (result.success) {
          // Refetch all settings silently to pick up any backend-triggered changes
          // (e.g., enabling a metadata provider auto-updates METADATA_PROVIDER)
          await fetchSettings({ silent: true, force: true });
        }

        return result;
      } catch (err) {
        if (Object.keys(valuesToSave).length > 0) {
          try {
            const response = await loadSettingsIntoCache({ force: true });
            if (settingsTabMatchesSavedValues(tabName, response.tabs, valuesToSave)) {
              setError(null);
              applySettingsResponse(response);
              return {
                success: true,
                message: t(
                  'Settings saved, but the proxy interrupted the response. Latest values were confirmed.',
                ),
                updated: Object.keys(valuesToSave),
                requiresRestart: restartRequiredFor.length > 0,
                restartRequiredFor,
              };
            }
          } catch (recoveryError) {
            console.warn('Failed to verify settings save after response error:', recoveryError);
          }
        }

        console.error('Failed to save settings tab:', tabName, err);
        return {
          success: false,
          message: err instanceof Error ? err.message : t('Failed to save settings'),
          updated: [],
        };
      } finally {
        setIsSaving(false);
      }
    },
    [applySettingsResponse, fetchSettings, originalValues, tabs, values],
  );

  const executeAction = useCallback(
    async (tabName: string, actionKey: string): Promise<ActionResult> => {
      try {
        // Pass current form values so action can use unsaved values
        const currentValues = values[tabName] || {};
        const result = await executeSettingsAction(tabName, actionKey, currentValues);

        // Re-fetch settings after successful action to pick up updated options
        // (e.g., Grimmory "Test Connection" refreshes library/path lists)
        if (result.success) {
          await fetchSettings({ silent: true, preserveDirtyValues: true, force: true });
        }

        return result;
      } catch (err) {
        console.error('Action execution failed:', tabName, actionKey, err);
        return {
          success: false,
          message: err instanceof Error ? err.message : t('Action failed'),
        };
      }
    },
    [values, fetchSettings],
  );

  return {
    tabs,
    groups,
    isLoading,
    error,
    selectedTab,
    setSelectedTab,
    values,
    updateValue,
    hasChanges,
    saveTab,
    executeAction,
    isSaving,
    refetch: () => fetchSettings({ force: true }),
  };
}

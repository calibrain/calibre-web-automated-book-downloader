import { useState, useCallback, useEffect } from 'react';
import { getSettings, updateSettings, executeSettingsAction } from '../services/api';
import {
  SettingsTab,
  SettingsGroup,
  SettingsField,
  SelectFieldConfig,
  ActionResult,
  UpdateResult,
} from '../types/settings';

// Client-side only theme field that gets injected into the general tab
const THEME_FIELD: SelectFieldConfig = {
  type: 'SelectField',
  key: '_THEME',
  label: 'Theme',
  description: 'Choose your preferred color scheme.',
  value: 'auto', // Placeholder, actual value comes from localStorage
  options: [
    { value: 'light', label: 'Light' },
    { value: 'dark', label: 'Dark' },
    { value: 'auto', label: 'Auto (System)' },
  ],
};

// Apply theme to document
function applyTheme(theme: string): void {
  const effectiveTheme = theme === 'auto'
    ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : theme;
  document.documentElement.setAttribute('data-theme', effectiveTheme);
}

// Extract value from a field based on its type
function getFieldValue(field: SettingsField): unknown {
  // These field types have no value property
  if (field.type === 'ActionButton' || field.type === 'HeadingField') {
    return undefined;
  }
  // All other fields have a value property
  return field.value ?? '';
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

export function useSettings(): UseSettingsReturn {
  const [tabs, setTabs] = useState<SettingsTab[]>([]);
  const [groups, setGroups] = useState<SettingsGroup[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedTab, setSelectedTab] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, Record<string, unknown>>>({});
  const [originalValues, setOriginalValues] = useState<Record<string, Record<string, unknown>>>({});
  const [isSaving, setIsSaving] = useState(false);

  const fetchSettings = useCallback(async (silent = false) => {
    if (!silent) {
      setIsLoading(true);
    }
    setError(null);
    try {
      const response = await getSettings();

      // Inject theme field into the general tab at the beginning
      const tabsWithTheme = response.tabs.map((tab) => {
        if (tab.name === 'general') {
          return {
            ...tab,
            fields: [THEME_FIELD, ...tab.fields],
          };
        }
        return tab;
      });
      setTabs(tabsWithTheme);
      setGroups(response.groups || []);

      // Initialize values from fetched data
      const initialValues: Record<string, Record<string, unknown>> = {};
      tabsWithTheme.forEach((tab) => {
        initialValues[tab.name] = {};
        tab.fields.forEach((field) => {
          if (field.type !== 'ActionButton') {
            // Special handling for theme field - get from localStorage
            if (field.key === '_THEME') {
              initialValues[tab.name][field.key] = localStorage.getItem('preferred-theme') || 'auto';
            } else {
              initialValues[tab.name][field.key] = getFieldValue(field);
            }
          }
        });
      });
      setValues(initialValues);
      setOriginalValues(JSON.parse(JSON.stringify(initialValues)));

      // Select first tab by default if none selected
      if (tabsWithTheme.length > 0) {
        setSelectedTab((current) => current ?? tabsWithTheme[0].name);
      }
    } catch (err) {
      console.error('Failed to fetch settings:', err);
      setError(err instanceof Error ? err.message : 'Failed to load settings');
    } finally {
      if (!silent) {
        setIsLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  const updateValue = useCallback((tabName: string, key: string, value: unknown) => {
    // Apply theme immediately when changed (no save button needed)
    if (key === '_THEME' && typeof value === 'string') {
      localStorage.setItem('preferred-theme', value);
      applyTheme(value);
      // Also update original value so it doesn't show as pending change
      setOriginalValues((prev) => ({
        ...prev,
        [tabName]: {
          ...prev[tabName],
          [key]: value,
        },
      }));
    }

    setValues((prev) => ({
      ...prev,
      [tabName]: {
        ...prev[tabName],
        [key]: value,
      },
    }));
  }, []);

  const hasChanges = useCallback(
    (tabName: string) => {
      const current = values[tabName];
      const original = originalValues[tabName];
      if (!current || !original) return false;

      const tab = tabs.find((t) => t.name === tabName);
      if (!tab) return false;

      for (const field of tab.fields) {
        if (field.type === 'ActionButton' || field.type === 'HeadingField') continue;

        const currentValue = current[field.key];
        const originalValue = original[field.key];

        // Compare values - works for all field types including password
        if (JSON.stringify(currentValue) !== JSON.stringify(originalValue)) {
          return true;
        }
      }

      return false;
    },
    [values, originalValues, tabs]
  );

  const saveTab = useCallback(
    async (tabName: string): Promise<UpdateResult> => {
      setIsSaving(true);
      try {
        const tabValues = values[tabName] || {};
        const originalTabValues = originalValues[tabName] || {};

        // Only send values that actually changed
        const tab = tabs.find((t) => t.name === tabName);
        const valuesToSave: Record<string, unknown> = {};

        if (tab) {
          for (const field of tab.fields) {
            if (field.type === 'ActionButton' || field.type === 'HeadingField') continue;
            if (field.fromEnv) continue; // Skip env-locked fields
            if (field.key === '_THEME') continue; // Skip client-side only theme field

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
        }

        const result = await updateSettings(tabName, valuesToSave);

        if (result.success) {
          // Refetch all settings silently to pick up any backend-triggered changes
          // (e.g., enabling a metadata provider auto-updates METADATA_PROVIDER)
          await fetchSettings(true);
        }

        return result;
      } catch (err) {
        console.error('Failed to save settings tab:', tabName, err);
        return {
          success: false,
          message: err instanceof Error ? err.message : 'Failed to save settings',
          updated: [],
        };
      } finally {
        setIsSaving(false);
      }
    },
    [values, originalValues, tabs]
  );

  const executeAction = useCallback(
    async (tabName: string, actionKey: string): Promise<ActionResult> => {
      try {
        // Pass current form values so action can use unsaved values
        const currentValues = values[tabName] || {};
        return await executeSettingsAction(tabName, actionKey, currentValues);
      } catch (err) {
        console.error('Action execution failed:', tabName, actionKey, err);
        return {
          success: false,
          message: err instanceof Error ? err.message : 'Action failed',
        };
      }
    },
    [values]
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
    refetch: fetchSettings,
  };
}

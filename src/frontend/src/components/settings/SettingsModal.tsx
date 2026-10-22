import { useState, useCallback, useRef, useMemo } from 'react';

import { useSearchMode } from '../../contexts/SearchModeContext';
import { useBodyScrollLock } from '../../hooks/useBodyScrollLock';
import { useEscapeKey } from '../../hooks/useEscapeKey';
import { useMediaQuery } from '../../hooks/useMediaQuery';
import { useMountEffect } from '../../hooks/useMountEffect';
import { useSettings } from '../../hooks/useSettings';
import { t } from '../../i18n';
import { getAdminSettingsOverridesSummary, getSettingsTab } from '../../services/api';
import { SettingsContent } from './SettingsContent';
import { SettingsHeader } from './SettingsHeader';
import { SettingsSidebar } from './SettingsSidebar';

interface SettingsModalProps {
  isOpen: boolean;
  authMode: string;
  onClose: () => void;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  onSettingsSaved?: () => void;
  onRefreshAuth?: () => Promise<void>;
}

function getStringValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function getStringArrayValue(value: unknown): string[] {
  return Array.isArray(value) && value.every((entry) => typeof entry === 'string') ? value : [];
}

function getBooleanValue(value: unknown): boolean {
  return typeof value === 'boolean' ? value : false;
}

export const SettingsModal = ({
  isOpen,
  authMode,
  onClose,
  onShowToast,
  onSettingsSaved,
  onRefreshAuth,
}: SettingsModalProps) => {
  const isMobile = useMediaQuery('(max-width: 767px)');
  const [isClosing, setIsClosing] = useState(false);
  const [sessionVersion, setSessionVersion] = useState(0);
  const mobileDetailStateRef = useRef<{
    isOpen: boolean;
    closeDetail: (() => void) | null;
  }>({
    isOpen: false,
    closeDetail: null,
  });

  const registerMobileDetailState = useCallback(
    (detailOpen: boolean, closeDetail: (() => void) | null) => {
      mobileDetailStateRef.current = { isOpen: detailOpen, closeDetail };
    },
    [],
  );

  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
      setSessionVersion((current) => current + 1);
    }, 150);
  }, [onClose]);

  const handleEscape = useCallback(() => {
    if (isMobile && mobileDetailStateRef.current.isOpen) {
      mobileDetailStateRef.current.closeDetail?.();
      return;
    }

    handleClose();
  }, [handleClose, isMobile]);

  useBodyScrollLock(isOpen);
  useEscapeKey(isOpen, handleEscape);

  if (!isOpen && !isClosing) return null;

  return (
    <SettingsModalSession
      key={sessionVersion}
      isOpen={isOpen}
      isClosing={isClosing}
      isMobile={isMobile}
      authMode={authMode}
      onClose={handleClose}
      onShowToast={onShowToast}
      onSettingsSaved={onSettingsSaved}
      onRefreshAuth={onRefreshAuth}
      onMobileDetailChange={registerMobileDetailState}
    />
  );
};

interface SettingsModalSessionProps {
  isOpen: boolean;
  isClosing: boolean;
  isMobile: boolean;
  authMode: string;
  onClose: () => void;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  onSettingsSaved?: () => void;
  onRefreshAuth?: () => Promise<void>;
  onMobileDetailChange: (isOpen: boolean, closeDetail: (() => void) | null) => void;
}

interface SettingsModalTabSyncProps {
  selectedTab: string;
  refreshOverrideSummaryForTab: (tabName: string) => Promise<void>;
  setSecurityAccessError: (message: string | null) => void;
}

const SettingsModalTabSync = ({
  selectedTab,
  refreshOverrideSummaryForTab,
  setSecurityAccessError,
}: SettingsModalTabSyncProps) => {
  useMountEffect(() => {
    let cancelled = false;

    void refreshOverrideSummaryForTab(selectedTab);

    if (selectedTab !== 'security') {
      setSecurityAccessError(null);
    } else {
      void getSettingsTab('security')
        .then(() => {
          if (!cancelled) {
            setSecurityAccessError(null);
          }
        })
        .catch((err) => {
          if (cancelled) {
            return;
          }

          const message = err instanceof Error ? err.message : 'Failed to load security settings';
          // API errors arrive translated.
          if (message.toLowerCase().includes(t('Admin access required').toLowerCase())) {
            setSecurityAccessError(message);
            return;
          }

          setSecurityAccessError(null);
        });
    }

    return () => {
      cancelled = true;
    };
  });

  return null;
};

const SettingsModalSession = ({
  isOpen,
  isClosing,
  isMobile,
  authMode,
  onClose,
  onShowToast,
  onSettingsSaved,
  onRefreshAuth,
  onMobileDetailChange,
}: SettingsModalSessionProps) => {
  const {
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
  } = useSettings();

  const { isUniversalMode } = useSearchMode();

  const [showMobileDetail, setShowMobileDetail] = useState(false);
  const [securityAccessError, setSecurityAccessError] = useState<string | null>(null);
  const [tabOverrideSummaries, setTabOverrideSummaries] = useState<
    Record<
      string,
      Record<
        string,
        { count: number; users: Array<{ userId: number; username: string; value: unknown }> }
      >
    >
  >({});
  const overrideSummaryRequestIdRef = useRef(0);

  const handleClose = useCallback(() => {
    setShowMobileDetail(false);
    onMobileDetailChange(false, null);
    onClose();
  }, [onClose, onMobileDetailChange]);

  const refreshOverrideSummaryForTab = useCallback(async (tabName: string) => {
    const requestId = ++overrideSummaryRequestIdRef.current;
    try {
      const data = await getAdminSettingsOverridesSummary(tabName);
      if (overrideSummaryRequestIdRef.current !== requestId) {
        return;
      }
      setTabOverrideSummaries((prev) => ({
        ...prev,
        [tabName]: data.keys || {},
      }));
    } catch {
      if (overrideSummaryRequestIdRef.current !== requestId) {
        return;
      }
      setTabOverrideSummaries((prev) => ({
        ...prev,
        [tabName]: {},
      }));
    }
  }, []);

  const handleBack = useCallback(() => {
    setShowMobileDetail(false);
    onMobileDetailChange(false, null);
  }, [onMobileDetailChange]);

  const handleSelectTab = useCallback(
    (tabName: string) => {
      setSelectedTab(tabName);
      if (isMobile) {
        setShowMobileDetail(true);
        onMobileDetailChange(true, handleBack);
      }
    },
    [handleBack, isMobile, onMobileDetailChange, setSelectedTab],
  );

  const handleRefreshCurrentTabOverrideSummary = useCallback(() => {
    if (!selectedTab) {
      return;
    }
    void refreshOverrideSummaryForTab(selectedTab);
  }, [selectedTab, refreshOverrideSummaryForTab]);

  const handleSave = useCallback(async () => {
    if (!selectedTab) return;
    const result = await saveTab(selectedTab);
    if (result.success) {
      void refreshOverrideSummaryForTab(selectedTab);
      onShowToast?.(result.message, 'success');
      onSettingsSaved?.();
      if (result.requiresRestart) {
        setTimeout(() => {
          onShowToast?.(t('Some settings require a container restart to take effect'), 'info');
        }, 500);
      }
    } else {
      onShowToast?.(result.message, 'error');
    }
  }, [selectedTab, saveTab, onShowToast, onSettingsSaved, refreshOverrideSummaryForTab]);

  const handleAction = useCallback(
    async (actionKey: string) => {
      if (!selectedTab) {
        return { success: false, message: t('No tab selected') };
      }

      if (selectedTab === 'security' && actionKey === 'open_users_tab') {
        setSelectedTab('users');
        if (isMobile) {
          setShowMobileDetail(true);
          onMobileDetailChange(true, handleBack);
        }
        return { success: true, message: t('Opening Users tab...') };
      }
      const result = await executeAction(selectedTab, actionKey);
      if (result.success) {
        void refreshOverrideSummaryForTab(selectedTab);
      }
      return result;
    },
    [
      handleBack,
      isMobile,
      onMobileDetailChange,
      refreshOverrideSummaryForTab,
      executeAction,
      selectedTab,
      setSelectedTab,
    ],
  );

  const handleFieldChange = useCallback(
    (key: string, value: unknown) => {
      if (!selectedTab) return;
      updateValue(selectedTab, key, value);

      if (selectedTab === 'security') {
        const tabValues = values[selectedTab] || {};
        const currentScopes = getStringArrayValue(tabValues['OIDC_SCOPES']);

        if (key === 'OIDC_USE_ADMIN_GROUP') {
          const groupClaim = getStringValue(tabValues['OIDC_GROUP_CLAIM'], 'groups');
          if (value === true && !currentScopes.includes(groupClaim)) {
            updateValue(selectedTab, 'OIDC_SCOPES', [...currentScopes, groupClaim]);
          } else if (value === false && currentScopes.includes(groupClaim)) {
            updateValue(
              selectedTab,
              'OIDC_SCOPES',
              currentScopes.filter((s) => s !== groupClaim),
            );
          }
        }

        if (key === 'OIDC_GROUP_CLAIM' && typeof value === 'string') {
          const useAdminGroup = getBooleanValue(tabValues['OIDC_USE_ADMIN_GROUP']);
          if (useAdminGroup) {
            const oldClaim = getStringValue(tabValues['OIDC_GROUP_CLAIM'], 'groups');
            const newScopes = currentScopes.filter((s) => s !== oldClaim);
            if (value && !newScopes.includes(value)) {
              newScopes.push(value);
            }
            updateValue(selectedTab, 'OIDC_SCOPES', newScopes);
          }
        }
      }
    },
    [selectedTab, updateValue, values],
  );

  const currentTabHasChanges = useMemo(
    () => (selectedTab ? hasChanges(selectedTab) : false),
    [selectedTab, hasChanges],
  );

  if (!isOpen && !isClosing) return null;

  const currentTab = tabs.find((tab) => tab.name === selectedTab);
  const currentTabDisplayName = currentTab?.displayName || t('Settings');
  const selectedTabSyncKey = isOpen && selectedTab ? selectedTab : null;
  const tabSync = selectedTabSyncKey ? (
    <SettingsModalTabSync
      key={selectedTabSyncKey}
      selectedTab={selectedTabSyncKey}
      refreshOverrideSummaryForTab={refreshOverrideSummaryForTab}
      setSecurityAccessError={setSecurityAccessError}
    />
  ) : null;
  const selectedAuthMethod = values.security?.AUTH_METHOD;
  const usersAuthMode = typeof selectedAuthMethod === 'string' ? selectedAuthMethod : authMode;
  const currentTabContent = currentTab ? (
    selectedTab === 'security' && securityAccessError ? (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 p-8">
        <p className="text-sm opacity-60">{securityAccessError}</p>
      </div>
    ) : (
      <SettingsContent
        tab={currentTab}
        values={values[currentTab.name] || {}}
        onChange={handleFieldChange}
        onSave={handleSave}
        onAction={handleAction}
        isSaving={isSaving}
        hasChanges={currentTabHasChanges}
        isUniversalMode={isUniversalMode}
        overrideSummary={tabOverrideSummaries[currentTab.name]}
        customFieldContext={{
          authMode: usersAuthMode,
          onShowToast,
          onRefreshOverrideSummary: handleRefreshCurrentTabOverrideSummary,
          onRefreshAuth,
          onSettingsSaved,
        }}
      />
    )
  ) : null;

  if (isLoading) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        {tabSync}
        <button
          type="button"
          className="absolute inset-0 bg-black/60"
          style={{ willChange: 'opacity', contain: 'strict' }}
          onClick={handleClose}
          tabIndex={-1}
          aria-label={t('Close settings')}
        />
        <div
          className="relative rounded-xl bg-(--bg) p-8 shadow-2xl"
          style={{ background: 'var(--bg)' }}
        >
          <div className="flex items-center gap-3">
            <svg className="h-5 w-5 animate-spin" viewBox="0 0 24 24">
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
                fill="none"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
              />
            </svg>
            <span>{t('Loading settings...')}</span>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        {tabSync}
        <button
          type="button"
          className="absolute inset-0 bg-black/60"
          style={{ willChange: 'opacity', contain: 'strict' }}
          onClick={handleClose}
          tabIndex={-1}
          aria-label={t('Close settings')}
        />
        <div
          className="relative max-w-md rounded-xl bg-(--bg) p-8 shadow-2xl"
          style={{ background: 'var(--bg)' }}
        >
          <div className="space-y-4 text-center">
            <div className="text-red-500">
              <svg
                className="mx-auto h-12 w-12"
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth={1.5}
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"
                />
              </svg>
            </div>
            <p className="text-sm">{error}</p>
            <button
              type="button"
              onClick={handleClose}
              className="rounded-lg border border-(--border-muted) bg-(--bg-soft) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--hover-surface)"
            >
              {t('Close')}
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (isMobile) {
    return (
      <>
        {tabSync}
        <div
          className={`fixed inset-0 z-50 flex flex-col ${isClosing ? 'animate-fade-out' : 'animate-fade-in'}`}
          style={{ background: 'var(--bg)' }}
        >
          {!showMobileDetail ? (
            <>
              <SettingsHeader title={t('Settings')} onClose={handleClose} />
              <SettingsSidebar
                tabs={tabs}
                groups={groups}
                selectedTab={selectedTab}
                onSelectTab={handleSelectTab}
                mode="list"
              />
            </>
          ) : (
            <>
              <SettingsHeader
                title={currentTabDisplayName}
                showBack
                onBack={handleBack}
                onClose={handleClose}
              />
              {currentTabContent}
            </>
          )}
        </div>
      </>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {tabSync}
      <button
        type="button"
        className={`absolute inset-0 bg-black/60 transition-opacity duration-150 ${isClosing ? 'opacity-0' : 'opacity-100'}`}
        style={{ willChange: 'opacity', contain: 'strict' }}
        onClick={handleClose}
        tabIndex={-1}
        aria-label={t('Close settings')}
      />

      <div
        className={`relative flex h-[85vh] max-h-[750px] w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-(--border-muted) shadow-2xl ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        style={{ background: 'var(--bg)' }}
        role="dialog"
        aria-modal="true"
        aria-label={t('Settings')}
      >
        <SettingsHeader title={t('Settings')} onClose={handleClose} />

        <div className="flex min-h-0 flex-1">
          <SettingsSidebar
            tabs={tabs}
            groups={groups}
            selectedTab={selectedTab}
            onSelectTab={handleSelectTab}
            mode="sidebar"
          />

          {currentTabContent ?? (
            <div className="flex flex-1 items-center justify-center text-sm opacity-60">
              {t('Select a category to configure')}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

import { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import { useSettings } from '../../hooks/useSettings';
import { useSearchMode } from '../../contexts/SearchModeContext';
import { SettingsHeader } from './SettingsHeader';
import { SettingsSidebar } from './SettingsSidebar';
import { SettingsContent } from './SettingsContent';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  onSettingsSaved?: () => void;
}

export const SettingsModal = ({ isOpen, onClose, onShowToast, onSettingsSaved }: SettingsModalProps) => {
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

  // Track if we're showing detail view on mobile
  const [isMobile, setIsMobile] = useState(false);
  const [showMobileDetail, setShowMobileDetail] = useState(false);
  const [isClosing, setIsClosing] = useState(false);

  // Track previous isOpen state to detect modal open transition
  const prevIsOpenRef = useRef(false);

  // Check for mobile viewport
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768);
    };
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
    }, 150);
  }, [onClose]);

  // Handle ESC key
  useEffect(() => {
    if (!isOpen) return;

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (isMobile && showMobileDetail) {
          setShowMobileDetail(false);
        } else {
          handleClose();
        }
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [isOpen, isMobile, showMobileDetail, handleClose]);

  // Prevent body scroll when open
  useEffect(() => {
    if (isOpen) {
      const previousOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = previousOverflow;
      };
    }
  }, [isOpen]);

  // Reset mobile detail view when modal opens
  useEffect(() => {
    if (isOpen) {
      setShowMobileDetail(false);
      setIsClosing(false);
    }
  }, [isOpen]);

  // Reset to first tab when modal transitions from closed to open
  useEffect(() => {
    const justOpened = isOpen && !prevIsOpenRef.current;
    prevIsOpenRef.current = isOpen;

    // On desktop, select first tab when modal opens (reset on each open)
    if (justOpened && !isMobile && tabs.length > 0) {
      setSelectedTab(tabs[0].name);
    }
  }, [isOpen, isMobile, tabs, setSelectedTab]);

  const handleSelectTab = useCallback(
    (tabName: string) => {
      setSelectedTab(tabName);
      if (isMobile) {
        setShowMobileDetail(true);
      }
    },
    [isMobile, setSelectedTab]
  );

  const handleBack = useCallback(() => {
    setShowMobileDetail(false);
  }, []);

  const handleSave = useCallback(async () => {
    if (!selectedTab) return;
    const result = await saveTab(selectedTab);
    if (result.success) {
      onShowToast?.(result.message, 'success');
      // Notify parent that settings were saved so it can refresh config
      onSettingsSaved?.();
      // Show additional toast if some settings require restart
      if (result.requiresRestart) {
        setTimeout(() => {
          onShowToast?.('Some settings require a container restart to take effect', 'info');
        }, 500);
      }
    } else {
      onShowToast?.(result.message, 'error');
    }
  }, [selectedTab, saveTab, onShowToast, onSettingsSaved]);

  const handleAction = useCallback(
    async (actionKey: string) => {
      if (!selectedTab) {
        return { success: false, message: 'No tab selected' };
      }
      return executeAction(selectedTab, actionKey);
    },
    [selectedTab, executeAction]
  );

  // Memoize the field change handler to prevent creating new functions on every render
  const handleFieldChange = useCallback(
    (key: string, value: unknown) => {
      if (selectedTab) {
        updateValue(selectedTab, key, value);
      }
    },
    [selectedTab, updateValue]
  );

  // Memoize hasChanges to avoid expensive JSON.stringify comparisons on every render
  // Must be before early returns to satisfy React's rules of hooks
  const currentTabHasChanges = useMemo(
    () => (selectedTab ? hasChanges(selectedTab) : false),
    [selectedTab, hasChanges, values]
  );

  if (!isOpen && !isClosing) return null;

  const currentTab = tabs.find((t) => t.name === selectedTab);
  const currentTabDisplayName = currentTab?.displayName || 'Settings';

  // Loading state
  if (isLoading) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        <div
          className="absolute inset-0 bg-black/50 backdrop-blur-[2px]"
          style={{ willChange: 'opacity', contain: 'strict' }}
          onClick={handleClose}
        />
        <div
          className="relative bg-[var(--bg)] rounded-xl p-8 shadow-2xl"
          style={{ background: 'var(--bg)' }}
        >
          <div className="flex items-center gap-3">
            <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
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
            <span>Loading settings...</span>
          </div>
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center">
        <div
          className="absolute inset-0 bg-black/50 backdrop-blur-[2px]"
          style={{ willChange: 'opacity', contain: 'strict' }}
          onClick={handleClose}
        />
        <div
          className="relative bg-[var(--bg)] rounded-xl p-8 shadow-2xl max-w-md"
          style={{ background: 'var(--bg)' }}
        >
          <div className="text-center space-y-4">
            <div className="text-red-500">
              <svg
                className="w-12 h-12 mx-auto"
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
              onClick={handleClose}
              className="px-4 py-2 rounded-lg text-sm font-medium
                         bg-[var(--bg-soft)] border border-[var(--border-muted)]
                         hover:bg-[var(--hover-surface)] transition-colors"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    );
  }

  // Mobile layout
  if (isMobile) {
    return (
      <div
        className={`fixed inset-0 z-50 flex flex-col
                    ${isClosing ? 'animate-fade-out' : 'animate-fade-in'}`}
        style={{ background: 'var(--bg)' }}
      >
        {!showMobileDetail ? (
          // Category list view
          <>
            <SettingsHeader title="Settings" onClose={handleClose} />
            <SettingsSidebar
              tabs={tabs}
              groups={groups}
              selectedTab={selectedTab}
              onSelectTab={handleSelectTab}
              mode="list"
            />
          </>
        ) : (
          // Detail view
          <>
            <SettingsHeader
              title={currentTabDisplayName}
              showBack
              onBack={handleBack}
              onClose={handleClose}
            />
            {currentTab && (
              <SettingsContent
                tab={currentTab}
                values={values[currentTab.name] || {}}
                onChange={handleFieldChange}
                onSave={handleSave}
                onAction={handleAction}
                isSaving={isSaving}
                hasChanges={currentTabHasChanges}
                isUniversalMode={isUniversalMode}
              />
            )}
          </>
        )}
      </div>
    );
  }

  // Desktop layout
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className={`absolute inset-0 bg-black/50 backdrop-blur-[2px] transition-opacity duration-150
                    ${isClosing ? 'opacity-0' : 'opacity-100'}`}
        style={{ willChange: 'opacity', contain: 'strict' }}
        onClick={handleClose}
      />

      {/* Modal */}
      <div
        className={`relative w-full max-w-4xl h-[85vh] rounded-xl
                    border border-[var(--border-muted)] shadow-2xl
                    flex flex-col overflow-hidden
                    ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        style={{ background: 'var(--bg)' }}
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
      >
        <SettingsHeader title="Settings" onClose={handleClose} />

        <div className="flex flex-1 min-h-0">
          <SettingsSidebar
            tabs={tabs}
            groups={groups}
            selectedTab={selectedTab}
            onSelectTab={handleSelectTab}
            mode="sidebar"
          />

          {currentTab ? (
            <SettingsContent
              tab={currentTab}
              values={values[currentTab.name] || {}}
              onChange={handleFieldChange}
              onSave={handleSave}
              onAction={handleAction}
              isSaving={isSaving}
              hasChanges={currentTabHasChanges}
              isUniversalMode={isUniversalMode}
            />
          ) : (
            <div className="flex-1 flex items-center justify-center text-sm opacity-60">
              Select a category to configure
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

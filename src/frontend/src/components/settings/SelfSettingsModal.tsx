import { useCallback, useMemo, useState } from 'react';

import { useBodyScrollLock } from '../../hooks/useBodyScrollLock';
import { useEscapeKey } from '../../hooks/useEscapeKey';
import { useMountEffect } from '../../hooks/useMountEffect';
import { t } from '../../i18n';
import type { AdminUser, DeliveryPreferencesResponse } from '../../services/api';
import {
  getSelfUserEditContext,
  testSelfNotificationPreferences,
  updateSelfUser,
} from '../../services/api';
import {
  getStoredThemePreference,
  setThemePreference,
  THEME_FIELD,
} from '../../utils/themePreference';
import { SelectField } from './fields';
import { FieldWrapper } from './shared';
import {
  DEFAULT_SELF_USER_OVERRIDE_SECTIONS,
  normalizeUserOverrideSections,
  UserOverridesSections,
} from './users';
import { UserAccountCardContent, UserEditActions, UserIdentityHeader } from './users/UserCard';
import { useUserOverridesState } from './users/useUserOverridesState';

interface SelfSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  onSettingsSaved?: () => void;
}

const MIN_PASSWORD_LENGTH = 4;

const getPasswordError = (
  password: string,
  passwordConfirm: string,
  currentPassword: string,
): string | null => {
  if (!password && !passwordConfirm) {
    return null;
  }
  if (!password) {
    return t('Password is required');
  }
  if (password.length < MIN_PASSWORD_LENGTH) {
    return t('Password must be at least {count} characters', { count: MIN_PASSWORD_LENGTH });
  }
  if (!currentPassword) {
    return t('Current password is required');
  }
  return password === passwordConfirm ? null : t('Passwords do not match');
};

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return fallback;
};

export const SelfSettingsModal = ({
  isOpen,
  onClose,
  onShowToast,
  onSettingsSaved,
}: SelfSettingsModalProps) => {
  const [isClosing, setIsClosing] = useState(false);
  const [sessionVersion, setSessionVersion] = useState(0);

  const handleRequestClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
      setSessionVersion((current) => current + 1);
    }, 150);
  }, [onClose]);

  useBodyScrollLock(isOpen);

  if (!isOpen && !isClosing) {
    return null;
  }

  return (
    <SelfSettingsModalSession
      key={sessionVersion}
      isOpen={isOpen}
      isClosing={isClosing}
      onRequestClose={handleRequestClose}
      onShowToast={onShowToast}
      onSettingsSaved={onSettingsSaved}
    />
  );
};

interface SelfSettingsModalSessionProps {
  isOpen: boolean;
  isClosing: boolean;
  onRequestClose: () => void;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  onSettingsSaved?: () => void;
}

const SelfSettingsModalSession = ({
  isOpen,
  isClosing,
  onRequestClose,
  onShowToast,
  onSettingsSaved,
}: SelfSettingsModalSessionProps) => {
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [originalUser, setOriginalUser] = useState<AdminUser | null>(null);
  const [deliveryPreferences, setDeliveryPreferences] =
    useState<DeliveryPreferencesResponse | null>(null);
  const [searchPreferences, setSearchPreferences] = useState<DeliveryPreferencesResponse | null>(
    null,
  );
  const [notificationPreferences, setNotificationPreferences] =
    useState<DeliveryPreferencesResponse | null>(null);
  const [visibleSections, setVisibleSections] = useState(DEFAULT_SELF_USER_OVERRIDE_SECTIONS);

  const [editPassword, setEditPassword] = useState('');
  const [editPasswordConfirm, setEditPasswordConfirm] = useState('');
  const [editCurrentPassword, setEditCurrentPassword] = useState('');

  const [themeValue, setThemeValue] = useState(getStoredThemePreference());

  const preferenceGroups = useMemo(
    () => [deliveryPreferences, searchPreferences, notificationPreferences],
    [deliveryPreferences, searchPreferences, notificationPreferences],
  );
  const {
    userSettings,
    setUserSettings,
    isUserOverridable,
    currentSettingsPayload,
    hasUserSettingsChanges: hasSettingsChanges,
    applyUserOverridesContext,
  } = useUserOverridesState({ preferenceGroups });

  const loadEditContext = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const context = await getSelfUserEditContext();

      setEditingUser(context.user);
      setOriginalUser(context.user);
      setDeliveryPreferences(context.deliveryPreferences || null);
      setSearchPreferences(context.searchPreferences || null);
      setNotificationPreferences(context.notificationPreferences || null);
      setVisibleSections(
        normalizeUserOverrideSections(context.visibleUserSettingsSections, 'self'),
      );
      applyUserOverridesContext({
        settings: context.user.settings || {},
        userOverridableKeys: context.userOverridableKeys || [],
      });
      setEditPassword('');
      setEditPasswordConfirm('');
      setEditCurrentPassword('');
    } catch (error) {
      setLoadError(getErrorMessage(error, t('Failed to load account settings')));
    } finally {
      setIsLoading(false);
    }
  }, [applyUserOverridesContext]);

  useMountEffect(() => {
    void loadEditContext();
  });

  const handleClose = useCallback(() => {
    if (isSaving) {
      return;
    }
    onRequestClose();
  }, [isSaving, onRequestClose]);

  useEscapeKey(isOpen, handleClose);

  const hasProfileChanges = Boolean(
    editingUser &&
    originalUser &&
    (editingUser.email !== originalUser.email ||
      editingUser.display_name !== originalUser.display_name),
  );

  const hasPasswordChanges = editPassword.length > 0 || editPasswordConfirm.length > 0;
  const passwordError = getPasswordError(editPassword, editPasswordConfirm, editCurrentPassword);
  const hasChanges = hasSettingsChanges || hasProfileChanges || hasPasswordChanges;

  const handleTestNotificationRoutes = useCallback((routes: Array<Record<string, unknown>>) => {
    return testSelfNotificationPreferences(routes);
  }, []);

  const handleSave = useCallback(async () => {
    if (!editingUser || !originalUser) {
      return;
    }
    if (passwordError) {
      onShowToast?.(passwordError, 'error');
      return;
    }

    const payload: {
      email?: string | null;
      display_name?: string | null;
      password?: string;
      current_password?: string;
      settings?: Record<string, unknown>;
    } = {};

    if (editingUser.edit_capabilities.canEditEmail && editingUser.email !== originalUser.email) {
      payload.email = editingUser.email;
    }
    if (
      editingUser.edit_capabilities.canEditDisplayName &&
      editingUser.display_name !== originalUser.display_name
    ) {
      payload.display_name = editingUser.display_name;
    }
    if (editingUser.edit_capabilities.canSetPassword && editPassword) {
      payload.password = editPassword;
      payload.current_password = editCurrentPassword;
    }
    if (hasSettingsChanges) {
      payload.settings = currentSettingsPayload;
    }

    if (Object.keys(payload).length === 0) {
      return;
    }

    setIsSaving(true);
    try {
      await updateSelfUser(payload);
      onShowToast?.(t('Account updated'), 'success');
      onSettingsSaved?.();
      await loadEditContext();
    } catch (error) {
      onShowToast?.(getErrorMessage(error, t('Failed to update account')), 'error');
    } finally {
      setIsSaving(false);
    }
  }, [
    currentSettingsPayload,
    editingUser,
    hasSettingsChanges,
    loadEditContext,
    onSettingsSaved,
    onShowToast,
    originalUser,
    passwordError,
    editPassword,
    editCurrentPassword,
  ]);

  if (!isOpen && !isClosing) {
    return null;
  }

  const titleId = 'self-settings-modal-title';
  const hasCachedEditContext = Boolean(editingUser);
  const showInitialLoadingState = isLoading && !hasCachedEditContext;
  const showInitialLoadErrorState = Boolean(loadError) && !hasCachedEditContext;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        className={`absolute inset-0 bg-black/60 transition-opacity duration-150 ${isClosing ? 'opacity-0' : 'opacity-100'}`}
        onClick={handleClose}
        tabIndex={-1}
        aria-label={t('Close account settings')}
      />

      <div
        className={`relative flex h-[85vh] max-h-[750px] w-full max-w-3xl flex-col overflow-hidden rounded-xl border border-(--border-muted) shadow-2xl ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        style={{ background: 'var(--bg)' }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="flex items-center justify-between border-b border-(--border-muted) px-6 py-4">
          <h3 id={titleId} className="sr-only">
            {t('My Account')}
          </h3>
          {editingUser ? (
            <UserIdentityHeader user={editingUser} showAuthSource showInactiveState={false} />
          ) : (
            <div className="text-sm font-medium">{t('My Account')}</div>
          )}
          <div className="flex items-center">
            <button
              type="button"
              onClick={handleClose}
              className="hover-action rounded-full p-2 text-gray-500 transition-colors hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-50 dark:text-gray-400 dark:hover:text-gray-100"
              aria-label={t('Close account settings')}
              disabled={isSaving}
            >
              <svg
                className="h-5 w-5"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                strokeWidth={1.5}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          {(() => {
            if (showInitialLoadingState) {
              return (
                <div className="flex h-full items-center justify-center text-sm opacity-60">
                  {t('Loading account settings...')}
                </div>
              );
            }

            if (showInitialLoadErrorState) {
              return (
                <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
                  <p className="text-sm opacity-70">{loadError}</p>
                  <button
                    type="button"
                    onClick={() => {
                      void loadEditContext();
                    }}
                    className="rounded-lg border border-(--border-muted) bg-(--bg-soft) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--hover-surface)"
                  >
                    {t('Retry')}
                  </button>
                </div>
              );
            }

            if (editingUser) {
              return (
                <div className="space-y-5">
                  <FieldWrapper field={THEME_FIELD}>
                    <SelectField
                      field={THEME_FIELD}
                      value={themeValue}
                      onChange={(value) => {
                        setThemeValue(value);
                        setThemePreference(value);
                      }}
                    />
                  </FieldWrapper>

                  <UserAccountCardContent
                    user={editingUser}
                    onUserChange={setEditingUser}
                    onSave={() => undefined}
                    saving={isSaving}
                    onCancel={handleClose}
                    hideEditActions
                    editPassword={editPassword}
                    onEditPasswordChange={setEditPassword}
                    editPasswordConfirm={editPasswordConfirm}
                    onEditPasswordConfirmChange={setEditPasswordConfirm}
                    editCurrentPassword={editCurrentPassword}
                    onEditCurrentPasswordChange={setEditCurrentPassword}
                    preferencesPlacement="after"
                    preferencesPanel={{
                      hideTitle: true,
                      children: (
                        <UserOverridesSections
                          scope="self"
                          sections={visibleSections}
                          deliveryPreferences={deliveryPreferences}
                          searchPreferences={searchPreferences}
                          notificationPreferences={notificationPreferences}
                          isUserOverridable={isUserOverridable}
                          userSettings={userSettings}
                          setUserSettings={setUserSettings}
                          onTestNotificationRoutes={handleTestNotificationRoutes}
                        />
                      ),
                    }}
                  />
                </div>
              );
            }

            return (
              <div className="flex h-full items-center justify-center text-sm opacity-60">
                {t('Unable to load account details.')}
              </div>
            );
          })()}
        </div>

        <footer className="flex items-center justify-end gap-3 border-t border-(--border-muted) px-6 py-4">
          <UserEditActions
            variant="modalFooter"
            onSave={() => {
              void handleSave();
            }}
            saving={isSaving}
            saveDisabled={!hasChanges || isSaving || isLoading}
            onCancel={handleClose}
            cancelDisabled={isSaving}
          />
        </footer>
      </div>
    </div>
  );
};

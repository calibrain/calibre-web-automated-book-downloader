import { useState } from 'react';

import { t } from '../../../i18n';
import type { AdminUser, DeliveryPreferencesResponse } from '../../../services/api';
import {
  createAdminUser,
  deleteAdminUser,
  syncAdminCwaUsers,
  updateAdminUser,
} from '../../../services/api';
import { buildUserSettingsPayload } from './settingsPayload';
import type { CreateUserFormState, PerUserSettings } from './types';

const MIN_PASSWORD_LENGTH = 4;
interface UseUserMutationsParams {
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  fetchUsers: (options?: { force?: boolean }) => Promise<AdminUser[]>;
  users: AdminUser[];
  createForm: CreateUserFormState;
  resetCreateForm: () => void;
  editingUser: AdminUser | null;
  editPassword: string;
  editPasswordConfirm: string;
  userSettings: PerUserSettings;
  userOverridableSettings: Set<string>;
  deliveryPreferences: DeliveryPreferencesResponse | null;
  searchPreferences: DeliveryPreferencesResponse | null;
  notificationPreferences: DeliveryPreferencesResponse | null;
  onEditSaveSuccess?: () => void;
}

interface SaveEditedUserOptions {
  includeProfile?: boolean;
  includePassword?: boolean;
  includeSettings?: boolean;
}

const getPasswordError = (password: string, passwordConfirm: string) => {
  if (!password) return null;
  if (password.length < MIN_PASSWORD_LENGTH)
    return t('Password must be at least {count} characters', { count: MIN_PASSWORD_LENGTH });
  return password === passwordConfirm ? null : t('Passwords do not match');
};

const authSourceLabel: Record<AdminUser['auth_source'], string> = {
  builtin: 'Local',
  oidc: 'OIDC',
  proxy: 'Proxy',
  cwa: 'CWA',
};

export const useUserMutations = ({
  onShowToast,
  fetchUsers,
  users,
  createForm,
  resetCreateForm,
  editingUser,
  editPassword,
  editPasswordConfirm,
  userSettings,
  userOverridableSettings,
  deliveryPreferences,
  searchPreferences,
  notificationPreferences,
  onEditSaveSuccess,
}: UseUserMutationsParams) => {
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deletingUserId, setDeletingUserId] = useState<number | null>(null);
  const [syncingCwa, setSyncingCwa] = useState(false);
  const fail = (message: string) => (onShowToast?.(message, 'error'), false);

  const createUser = async () => {
    if (!createForm.username || !createForm.password)
      return fail(t('Username and password are required'));
    const createPasswordError = getPasswordError(createForm.password, createForm.password_confirm);
    if (createPasswordError) return fail(createPasswordError);

    setCreating(true);
    try {
      const created = await createAdminUser({
        username: createForm.username,
        password: createForm.password,
        email: createForm.email || undefined,
        display_name: createForm.display_name || undefined,
        role: createForm.role || undefined,
      });
      resetCreateForm();
      onShowToast?.(t('Local user {username} created', { username: created.username }), 'success');
      await fetchUsers({ force: true });
      return true;
    } catch (err) {
      return fail(err instanceof Error ? err.message : t('Failed to create user'));
    } finally {
      setCreating(false);
    }
  };

  const saveEditedUser = async ({
    includeProfile = true,
    includePassword = true,
    includeSettings = true,
  }: SaveEditedUserOptions = {}) => {
    if (!editingUser) return false;
    const passwordError = includePassword
      ? getPasswordError(editPassword, editPasswordConfirm)
      : null;
    if (passwordError) return fail(passwordError);

    const caps = editingUser.edit_capabilities;
    const settingsPayload = includeSettings
      ? buildUserSettingsPayload(userSettings, userOverridableSettings, [
          deliveryPreferences,
          searchPreferences,
          notificationPreferences,
        ])
      : null;
    const updatePayload: Partial<Pick<AdminUser, 'role' | 'email' | 'display_name'>> & {
      password?: string;
      settings?: Record<string, unknown>;
    } = {};

    if (includeProfile) {
      if (caps.canEditEmail) {
        updatePayload.email = editingUser.email;
      }
      if (caps.canEditDisplayName) {
        updatePayload.display_name = editingUser.display_name;
      }
      if (caps.canEditRole) {
        updatePayload.role = editingUser.role;
      }
    }
    if (includePassword && caps.canSetPassword && editPassword) {
      updatePayload.password = editPassword;
    }
    if (settingsPayload && Object.keys(settingsPayload).length > 0) {
      updatePayload.settings = settingsPayload;
    }

    setSaving(true);
    try {
      await updateAdminUser(editingUser.id, updatePayload);
      onEditSaveSuccess?.();
      onShowToast?.(
        includeSettings && !includeProfile && !includePassword
          ? t('User preferences updated')
          : t('User updated'),
        'success',
      );
      await fetchUsers({ force: true });
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : t('Failed to update user');
      return fail(t('Failed to update user: {error}', { error: message }));
    } finally {
      setSaving(false);
    }
  };

  const deleteUser = async (userId: number) => {
    const deletedUser = users.find((user) => user.id === userId) || null;
    setDeletingUserId(userId);
    try {
      await deleteAdminUser(userId);
      onShowToast?.(t('User deleted'), 'success');
      await fetchUsers({ force: true });
      if (deletedUser && deletedUser.auth_source !== 'builtin') {
        onShowToast?.(
          t(
            '{source} users may be re-provisioned by your authentication source on a future login or sync.',
            { source: t(authSourceLabel[deletedUser.auth_source]) },
          ),
          'info',
        );
      }
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : t('Failed to delete user');
      return fail(t('Failed to delete user: {error}', { error: message }));
    } finally {
      setDeletingUserId(null);
    }
  };

  const syncCwaUsers = async () => {
    setSyncingCwa(true);
    try {
      const result = await syncAdminCwaUsers();
      onShowToast?.(result.message ? t(result.message) : t('Users synced from CWA'), 'success');
      await fetchUsers({ force: true });
      return true;
    } catch (err) {
      return fail(err instanceof Error ? err.message : t('Failed to sync users from CWA'));
    } finally {
      setSyncingCwa(false);
    }
  };

  return {
    creating,
    saving,
    deletingUserId,
    syncingCwa,
    createUser,
    saveEditedUser,
    deleteUser,
    syncCwaUsers,
  };
};

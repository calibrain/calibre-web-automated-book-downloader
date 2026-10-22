import { useState } from 'react';

import { t } from '../../../i18n';
import type { AdminUser, DownloadDefaults } from '../../../services/api';
import type { CreateUserFormState } from './types';
import { canCreateLocalUsersForAuthMode } from './types';
import {
  UserAccountCardContent,
  UserCreateCard,
  UserIdentityHeader,
  UserRoleControl,
} from './UserCard';

interface UserListViewProps {
  authMode: string;
  users: AdminUser[];
  loadingUsers: boolean;
  loadError: string | null;
  onRetryLoadUsers: () => void;
  needsLocalAdmin: boolean;
  onCreate: () => void;
  showCreateForm: boolean;
  createForm: CreateUserFormState;
  onCreateFormChange: (form: CreateUserFormState) => void;
  creating: boolean;
  isFirstUser: boolean;
  onCreateSubmit: () => void;
  onCancelCreate: () => void;
  showEditForm: boolean;
  activeEditUserId: number | null;
  editingUser: AdminUser | null;
  onEditingUserChange: (user: AdminUser) => void;
  onEditSave: () => void;
  saving: boolean;
  onCancelEdit: () => void;
  editPassword: string;
  onEditPasswordChange: (value: string) => void;
  editPasswordConfirm: string;
  onEditPasswordConfirmChange: (value: string) => void;
  downloadDefaults: DownloadDefaults | null;
  onOpenOverrides: () => void;
  onEdit: (user: AdminUser) => void;
  onDelete: (userId: number) => Promise<boolean>;
  deletingUserId: number | null;
  onSyncCwa: () => Promise<void> | void;
  syncingCwa: boolean;
}

export const UserListView = ({
  authMode,
  users,
  loadingUsers,
  loadError,
  onRetryLoadUsers,
  needsLocalAdmin,
  onCreate,
  showCreateForm,
  createForm,
  onCreateFormChange,
  creating,
  isFirstUser,
  onCreateSubmit,
  onCancelCreate,
  showEditForm,
  activeEditUserId,
  editingUser,
  onEditingUserChange,
  onEditSave,
  saving,
  onCancelEdit,
  editPassword,
  onEditPasswordChange,
  editPasswordConfirm,
  onEditPasswordConfirmChange,
  downloadDefaults,
  onOpenOverrides,
  onEdit,
  onDelete,
  deletingUserId,
  onSyncCwa,
  syncingCwa,
}: UserListViewProps) => {
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const canCreateLocalUsers = canCreateLocalUsersForAuthMode(authMode);
  const isCwaMode = (authMode || 'none').toLowerCase() === 'cwa';

  const handleDelete = async (userId: number) => {
    const ok = await onDelete(userId);
    if (ok) {
      setConfirmDelete(null);
    }
  };

  return (
    <div className="space-y-4">
      {(() => {
        if (loadingUsers && users.length === 0) {
          return (
            <div className="space-y-2 py-8 text-center">
              <p className="text-sm opacity-50">{t('Loading users...')}</p>
            </div>
          );
        }

        if (loadError && users.length === 0) {
          return (
            <div className="space-y-3 py-8 text-center">
              <p className="text-sm opacity-60">{loadError}</p>
              <button
                type="button"
                onClick={onRetryLoadUsers}
                className="rounded-lg border border-(--border-muted) bg-(--bg-soft) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--hover-surface)"
              >
                {t('Retry')}
              </button>
            </div>
          );
        }

        if (users.length === 0) {
          return (
            <div className="space-y-2 py-8 text-center">
              <p className="text-sm opacity-50">{t('No users yet.')}</p>
              <p className="text-xs opacity-40">
                {t(
                  'Create a local admin account before enabling OIDC to avoid getting locked out.',
                )}
              </p>
            </div>
          );
        }

        return (
          <div className="space-y-2">
            {users.map((user) => {
              const active = user.is_active;
              const isEditingRow = showEditForm && activeEditUserId === user.id;
              const hasLoadedEditUser = isEditingRow && editingUser?.id === user.id;
              const editorPanelId = `user-editor-${user.id}`;
              const toggleUserEditor = () => {
                setConfirmDelete(null);
                if (isEditingRow) {
                  onCancelEdit();
                  return;
                }

                onEdit(user);
              };
              return (
                <div
                  key={user.id}
                  className={`rounded-lg border border-(--border-muted) bg-(--bg-soft) transition-colors ${active ? '' : 'opacity-60'}`}
                >
                  <div
                    className={`hover-surface relative flex cursor-pointer flex-col gap-3 p-3 sm:flex-row sm:items-center sm:justify-between ${isEditingRow ? 'rounded-t-lg border-b border-(--border-muted)' : 'rounded-lg'}`}
                  >
                    <button
                      type="button"
                      onClick={toggleUserEditor}
                      aria-expanded={isEditingRow}
                      aria-controls={editorPanelId}
                      aria-label={
                        isEditingRow
                          ? t('Collapse user editor')
                          : t('Expand {username} editor', { username: user.username })
                      }
                      className={`absolute inset-0 appearance-none border-0 bg-transparent p-0 focus-visible:ring-2 focus-visible:ring-sky-500/50 focus-visible:outline-hidden ${isEditingRow ? 'rounded-t-lg' : 'rounded-lg'}`}
                    />

                    <div className="pointer-events-none relative z-10 min-w-0 flex-1">
                      <UserIdentityHeader user={user} />
                    </div>

                    <div className="pointer-events-none relative z-10 flex shrink-0 flex-wrap items-center gap-2 sm:justify-end">
                      <div className="pointer-events-auto">
                        {hasLoadedEditUser && editingUser ? (
                          <UserRoleControl
                            user={editingUser}
                            onUserChange={onEditingUserChange}
                            oidcAdminGroup={downloadDefaults?.OIDC_ADMIN_GROUP}
                            tooltipPosition="bottom"
                          />
                        ) : (
                          <UserRoleControl user={user} />
                        )}
                      </div>

                      <div
                        className="rounded-full p-2 text-gray-500 dark:text-gray-400"
                        aria-hidden="true"
                      >
                        <svg
                          xmlns="http://www.w3.org/2000/svg"
                          fill="none"
                          viewBox="0 0 24 24"
                          strokeWidth={1.5}
                          stroke="currentColor"
                          className={`h-[18px] w-[18px] transition-transform duration-200 ${isEditingRow ? 'rotate-180' : ''}`}
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="m19.5 8.25-7.5 7.5-7.5-7.5"
                          />
                        </svg>
                      </div>
                    </div>
                  </div>

                  {isEditingRow && (
                    <div id={editorPanelId} className="space-y-5 rounded-b-lg bg-(--bg) p-4">
                      {hasLoadedEditUser && editingUser ? (
                        <UserAccountCardContent
                          user={editingUser}
                          onUserChange={onEditingUserChange}
                          onSave={onEditSave}
                          saving={saving}
                          onCancel={onCancelEdit}
                          editPassword={editPassword}
                          onEditPasswordChange={onEditPasswordChange}
                          editPasswordConfirm={editPasswordConfirm}
                          onEditPasswordConfirmChange={onEditPasswordConfirmChange}
                          onDelete={() => setConfirmDelete(user.id)}
                          onConfirmDelete={() => {
                            void handleDelete(user.id);
                          }}
                          onCancelDelete={() => setConfirmDelete(null)}
                          isDeletePending={confirmDelete === user.id}
                          deleting={deletingUserId === user.id}
                          preferencesPanel={{
                            description: t(
                              'Customise delivery and request settings for this user.',
                            ),
                            actionLabel: t('Open User Preferences'),
                            onAction: onOpenOverrides,
                          }}
                        />
                      ) : (
                        <div className="text-sm opacity-60">{t('Loading user details...')}</div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        );
      })()}

      {canCreateLocalUsers && (
        <div>
          {showCreateForm ? (
            <UserCreateCard
              form={createForm}
              onChange={onCreateFormChange}
              creating={creating}
              isFirstUser={isFirstUser}
              needsLocalAdmin={needsLocalAdmin}
              onSubmit={onCreateSubmit}
              onCancel={onCancelCreate}
            />
          ) : (
            <button
              type="button"
              onClick={onCreate}
              className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-700"
            >
              {t('Create Local User')}
            </button>
          )}
        </div>
      )}

      {!canCreateLocalUsers && isCwaMode && (
        <div>
          <button
            type="button"
            onClick={() => {
              void onSyncCwa();
            }}
            disabled={syncingCwa}
            className="rounded-lg bg-sky-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {syncingCwa ? t('Syncing with CWA...') : t('Sync with CWA')}
          </button>
        </div>
      )}
    </div>
  );
};

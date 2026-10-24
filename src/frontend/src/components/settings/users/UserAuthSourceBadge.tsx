import { t } from '../../../i18n';
import type { AdminUser } from '../../../services/api';
import { AUTH_SOURCE_BADGE_CLASSES, AUTH_SOURCE_LABEL } from './types';

interface UserAuthSourceBadgeProps {
  user: AdminUser;
  showInactive?: boolean;
}

export const UserAuthSourceBadge = ({ user, showInactive = true }: UserAuthSourceBadgeProps) => {
  const authSource = user.auth_source;
  const active = user.is_active;
  const badgeBase =
    'inline-flex items-center rounded-md px-2.5 py-1 text-xs font-medium leading-none';

  return (
    <>
      <span className={`${badgeBase} ${AUTH_SOURCE_BADGE_CLASSES[authSource]}`}>
        {t(AUTH_SOURCE_LABEL[authSource])}
      </span>
      {showInactive && !active && (
        <span className={`${badgeBase} bg-zinc-500/10 opacity-80`}>{t('Inactive')}</span>
      )}
    </>
  );
};

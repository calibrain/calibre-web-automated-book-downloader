import { t } from '../i18n';

export interface ActivityStatusCounts {
  ongoing: number;
  completed: number;
  errored: number;
  pendingRequests: number;
}

interface ActivityBadgeState {
  total: number;
  colorClass: string;
  title: string;
}

export const getActivityBadgeState = (
  statusCounts: ActivityStatusCounts,
  isAdmin: boolean,
): ActivityBadgeState | null => {
  const pendingRequests = isAdmin ? statusCounts.pendingRequests : 0;
  const total =
    statusCounts.ongoing + statusCounts.completed + statusCounts.errored + pendingRequests;

  if (total <= 0) {
    return null;
  }

  let colorClass = 'bg-green-500';
  if (statusCounts.errored > 0) {
    colorClass = 'bg-red-500';
  } else if (statusCounts.ongoing > 0) {
    colorClass = 'bg-blue-500';
  } else if (pendingRequests > 0) {
    colorClass = 'bg-amber-500';
  }

  const counts = { ...statusCounts, pending: pendingRequests };
  const title = isAdmin
    ? t(
        '{ongoing} ongoing, {completed} completed, {errored} failed, {pending} pending requests',
        counts,
      )
    : t('{ongoing} ongoing, {completed} completed, {errored} failed', counts);

  return {
    total,
    colorClass,
    title,
  };
};

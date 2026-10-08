import type { RequestRecord } from '../../types';
import { isActiveDownloadStatus } from './activityStyles.js';
import type { ActivityItem, ActivityVisualStatus } from './activityTypes';

export type ActivityCardAction =
  | {
      kind: 'download-remove' | 'download-stop' | 'download-dismiss' | 'download-retry';
      bookId: string;
      linkedRequestId?: number;
    }
  | {
      kind: 'request-approve';
      requestId: number;
      record: RequestRecord;
    }
  | {
      kind: 'request-reject' | 'request-reopen' | 'request-cancel' | 'request-dismiss';
      requestId: number;
    };

interface ActivityCardBadge {
  key: 'download' | 'request' | 'status';
  text: string;
  visualStatus: ActivityVisualStatus;
  isActiveDownload: boolean;
  progress?: number;
}

interface ActivityCardModel {
  badges: ActivityCardBadge[];
  noteLine?: string;
  actions: ActivityCardAction[];
}

const formatDownloadProgress = (progress: number, sizeRaw?: string): string => {
  if (sizeRaw) {
    const sizeValue = parseFloat(sizeRaw.replace(/[^\d.]/g, ''));
    const sizeUnit = sizeRaw.replace(/[\d.\s]/g, '');
    if (sizeValue > 0) {
      const downloaded = (progress / 100) * sizeValue;
      return `${downloaded.toFixed(1)}${sizeUnit} / ${sizeRaw}`;
    }
  }
  return `Downloading ${Math.round(progress)}%`;
};

const toRequestVisualStatus = (status: RequestRecord['status']): ActivityVisualStatus => {
  if (status === 'pending') return 'pending';
  if (status === 'fulfilled') return 'fulfilled';
  if (status === 'rejected') return 'rejected';
  return 'cancelled';
};

const getPendingRequestText = (item: ActivityItem, isAdmin: boolean): string => {
  if (!isAdmin) {
    return 'Awaiting review';
  }
  const username = item.username?.trim() || item.requestRecord?.username?.trim();
  return username ? `Needs review · ${username}` : 'Needs review';
};

const getRequestBadge = (item: ActivityItem, isAdmin: boolean): ActivityCardBadge => {
  const requestVisualStatus = item.requestRecord
    ? toRequestVisualStatus(item.requestRecord.status)
    : item.visualStatus;
  const failureReason = item.requestRecord?.last_failure_reason?.trim() || null;
  const hasFailureReason = requestVisualStatus === 'pending' && Boolean(failureReason);
  const hasInFlightLinkedDownload =
    item.kind === 'download' &&
    requestVisualStatus === 'fulfilled' &&
    isActiveDownloadStatus(item.visualStatus);
  let visualStatus: ActivityVisualStatus = hasInFlightLinkedDownload
    ? 'resolving'
    : requestVisualStatus;
  if (hasFailureReason) {
    visualStatus = 'error';
  }

  let text = item.statusLabel;
  if (hasInFlightLinkedDownload) {
    text = 'Approved';
  } else if (hasFailureReason && failureReason) {
    text = failureReason;
  } else if (requestVisualStatus === 'pending') {
    text = getPendingRequestText(item, isAdmin);
  } else if (requestVisualStatus === 'fulfilled') {
    text = 'Approved';
  } else if (requestVisualStatus === 'rejected') {
    text = isAdmin ? 'Declined' : 'Not approved';
  } else if (requestVisualStatus === 'cancelled') {
    text = isAdmin ? 'Cancelled by requester' : 'Cancelled';
  }

  return {
    key: 'request',
    text,
    visualStatus,
    isActiveDownload: false,
  };
};

const getDownloadBadge = (item: ActivityItem): ActivityCardBadge => {
  let text = item.statusLabel;
  if (item.statusDetail) {
    text = item.statusDetail;
  } else if (item.visualStatus === 'downloading' && typeof item.progress === 'number') {
    text = formatDownloadProgress(item.progress, item.sizeRaw);
  }

  return {
    key: 'download',
    text,
    visualStatus: item.visualStatus,
    isActiveDownload: isActiveDownloadStatus(item.visualStatus),
    progress: item.progress,
  };
};

const buildBadges = (item: ActivityItem, isAdmin: boolean): ActivityCardBadge[] => {
  if (item.kind === 'download' && item.visualStatus === 'complete') {
    return [getDownloadBadge(item)];
  }

  if (item.kind === 'download' && item.requestId && item.requestRecord) {
    return [getRequestBadge(item, isAdmin), getDownloadBadge(item)];
  }

  if (item.kind === 'request') {
    return [getRequestBadge(item, isAdmin)];
  }

  return [getDownloadBadge(item)];
};

const buildRequestNoteLine = (item: ActivityItem): string | undefined => {
  const requestStatus = item.requestRecord?.status;
  if (item.requestNote && (requestStatus === 'pending' || item.visualStatus === 'pending')) {
    return `"${item.requestNote}"`;
  }
  if (
    item.adminNote &&
    (requestStatus === 'rejected' ||
      requestStatus === 'fulfilled' ||
      item.visualStatus === 'rejected' ||
      item.visualStatus === 'fulfilled')
  ) {
    return `"${item.adminNote}"`;
  }
  return undefined;
};

const buildActions = (item: ActivityItem, isAdmin: boolean): ActivityCardAction[] => {
  if (item.kind === 'download' && item.downloadBookId) {
    const canRetry = item.downloadRetryAvailable === true;
    if (item.visualStatus === 'queued') {
      return [{ kind: 'download-remove', bookId: item.downloadBookId }];
    }
    if (
      item.visualStatus === 'resolving' ||
      item.visualStatus === 'locating' ||
      item.visualStatus === 'downloading'
    ) {
      return [{ kind: 'download-stop', bookId: item.downloadBookId }];
    }
    if (item.visualStatus === 'error' && canRetry) {
      return [
        {
          kind: 'download-retry',
          bookId: item.downloadBookId,
        },
        {
          kind: 'download-dismiss',
          bookId: item.downloadBookId,
          linkedRequestId: item.requestId,
        },
      ];
    }
    if (item.visualStatus === 'cancelled' && canRetry) {
      return [
        {
          kind: 'download-retry',
          bookId: item.downloadBookId,
        },
        {
          kind: 'download-dismiss',
          bookId: item.downloadBookId,
          linkedRequestId: item.requestId,
        },
      ];
    }
    return [
      {
        kind: 'download-dismiss',
        bookId: item.downloadBookId,
        linkedRequestId: item.requestId,
      },
    ];
  }

  if (item.kind === 'request' && item.requestId) {
    if (item.visualStatus === 'pending') {
      if (isAdmin) {
        const actions: ActivityCardAction[] = [];
        if (item.requestRecord) {
          actions.push({
            kind: 'request-approve',
            requestId: item.requestId,
            record: item.requestRecord,
          });
        }
        actions.push({ kind: 'request-reject', requestId: item.requestId });
        return actions;
      }
      return [{ kind: 'request-cancel', requestId: item.requestId }];
    }

    if (item.visualStatus === 'rejected' && isAdmin) {
      return [
        { kind: 'request-reopen', requestId: item.requestId },
        { kind: 'request-dismiss', requestId: item.requestId },
      ];
    }

    if (
      item.visualStatus === 'fulfilled' ||
      item.visualStatus === 'rejected' ||
      item.visualStatus === 'cancelled'
    ) {
      return [{ kind: 'request-dismiss', requestId: item.requestId }];
    }
  }

  return [];
};

export const buildActivityCardModel = (item: ActivityItem, isAdmin: boolean): ActivityCardModel => {
  return {
    badges: buildBadges(item, isAdmin),
    noteLine: buildRequestNoteLine(item),
    actions: buildActions(item, isAdmin),
  };
};

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Socket } from 'socket.io-client';

import type { ActivityDismissTarget, ActivityItem } from '../components/activity';
import { downloadToActivityItem, requestToActivityItem } from '../components/activity';
import { dedupeHistoryItems } from '../components/activity/activityHistory.js';
import type { ActivityHistoryItem, ActivityDismissPayload } from '../services/api';
import {
  clearActivityHistory,
  dismissActivityItem,
  dismissManyActivityItems,
  getActivitySnapshot,
  listActivityHistory,
} from '../services/api';
import type { Book, RequestRecord, StatusData } from '../types';
import { isRecord } from '../utils/objectHelpers';
import { getActivityErrorMessage } from './useActivity.helpers.js';

const HISTORY_PAGE_SIZE = 50;

const parseTimestamp = (value: string | null | undefined, fallback: number = 0): number => {
  if (!value) {
    return fallback;
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const isRequestStatus = (value: unknown): value is RequestRecord['status'] => {
  return (
    value === 'pending' || value === 'fulfilled' || value === 'rejected' || value === 'cancelled'
  );
};

const isRequestLevel = (value: unknown): value is RequestRecord['request_level'] => {
  return value === 'book' || value === 'release';
};

const isPolicyMode = (value: unknown): value is RequestRecord['policy_mode'] => {
  return (
    value === 'download' ||
    value === 'request_release' ||
    value === 'request_book' ||
    value === 'blocked'
  );
};

const isNullableText = (value: unknown): value is string | null => {
  return value === null || typeof value === 'string';
};

const isNullableNumber = (value: unknown): value is number | null => {
  return value === null || (typeof value === 'number' && Number.isFinite(value));
};

const isDeliveryState = (value: unknown): value is RequestRecord['delivery_state'] => {
  return (
    value === undefined ||
    value === 'none' ||
    value === 'queued' ||
    value === 'resolving' ||
    value === 'locating' ||
    value === 'downloading' ||
    value === 'complete' ||
    value === 'error' ||
    value === 'cancelled'
  );
};

const parseHistoryBook = (value: unknown): Book | null => {
  if (!isRecord(value) || Array.isArray(value)) {
    return null;
  }

  const { id, title, author } = value;
  if (typeof id !== 'string' || typeof title !== 'string' || typeof author !== 'string') {
    return null;
  }

  return {
    id,
    title,
    author,
    ...(typeof value.request_id === 'number' && Number.isFinite(value.request_id)
      ? { request_id: value.request_id }
      : {}),
    ...(typeof value.format === 'string' ? { format: value.format } : {}),
    ...(typeof value.size === 'string' ? { size: value.size } : {}),
    ...(typeof value.preview === 'string' ? { preview: value.preview } : {}),
    ...(typeof value.download_path === 'string' ? { download_path: value.download_path } : {}),
    ...(typeof value.status_message === 'string' ? { status_message: value.status_message } : {}),
    ...(typeof value.source === 'string' ? { source: value.source } : {}),
    ...(typeof value.source_display_name === 'string'
      ? { source_display_name: value.source_display_name }
      : {}),
    ...(typeof value.username === 'string' ? { username: value.username } : {}),
    ...(typeof value.progress === 'number' && Number.isFinite(value.progress)
      ? { progress: value.progress }
      : {}),
    ...(typeof value.added_time === 'number' && Number.isFinite(value.added_time)
      ? { added_time: value.added_time }
      : {}),
    ...(typeof value.retry_available === 'boolean'
      ? { retry_available: value.retry_available }
      : {}),
    ...(typeof value.downloads === 'number' ? { downloads: value.downloads } : {}),
  };
};

const parseHistoryRequestRecord = (value: unknown): RequestRecord | null => {
  if (!isRecord(value) || Array.isArray(value)) {
    return null;
  }

  const { id, user_id, status, source_hint, content_type, request_level, policy_mode } = value;
  const book_data = value.book_data;
  const release_data = value.release_data;
  const note = value.note;
  const admin_note = value.admin_note;
  const reviewed_by = value.reviewed_by;
  const reviewed_at = value.reviewed_at;
  const created_at = value.created_at;
  const updated_at = value.updated_at;

  if (
    typeof id !== 'number' ||
    !Number.isFinite(id) ||
    typeof user_id !== 'number' ||
    !Number.isFinite(user_id) ||
    !isRequestStatus(status) ||
    !isNullableText(source_hint) ||
    (content_type !== 'ebook' && content_type !== 'audiobook') ||
    !isRequestLevel(request_level) ||
    !isPolicyMode(policy_mode) ||
    book_data === undefined ||
    release_data === undefined ||
    (book_data !== null && (!isRecord(book_data) || Array.isArray(book_data))) ||
    (release_data !== null && (!isRecord(release_data) || Array.isArray(release_data))) ||
    !isNullableText(note) ||
    !isNullableText(admin_note) ||
    !isNullableNumber(reviewed_by) ||
    !isNullableText(reviewed_at) ||
    typeof created_at !== 'string' ||
    typeof updated_at !== 'string' ||
    (value.username !== undefined && typeof value.username !== 'string') ||
    !isDeliveryState(value.delivery_state) ||
    (value.delivery_updated_at !== undefined &&
      value.delivery_updated_at !== null &&
      typeof value.delivery_updated_at !== 'string') ||
    (value.last_failure_reason !== undefined &&
      value.last_failure_reason !== null &&
      typeof value.last_failure_reason !== 'string')
  ) {
    return null;
  }

  return {
    id,
    user_id,
    status,
    source_hint,
    content_type,
    request_level,
    policy_mode,
    book_data,
    release_data,
    note,
    admin_note,
    reviewed_by,
    reviewed_at,
    created_at,
    updated_at,
    ...(value.username !== undefined ? { username: value.username } : {}),
    ...(value.delivery_state !== undefined ? { delivery_state: value.delivery_state } : {}),
    ...(value.delivery_updated_at !== undefined
      ? { delivery_updated_at: value.delivery_updated_at }
      : {}),
    ...(value.last_failure_reason !== undefined
      ? { last_failure_reason: value.last_failure_reason }
      : {}),
  };
};

const mapHistoryRowToActivityItem = (
  row: ActivityHistoryItem,
  viewerRole: 'user' | 'admin',
): ActivityItem => {
  const dismissedAtTs = parseTimestamp(row.dismissed_at);
  const snapshot = row.snapshot;
  if (snapshot && typeof snapshot === 'object') {
    const payload = snapshot;
    const historyBook = parseHistoryBook(payload.download);
    if (payload.kind === 'download' && historyBook) {
      const statusKey =
        row.final_status === 'error' || row.final_status === 'cancelled'
          ? row.final_status
          : 'complete';
      const downloadItem = downloadToActivityItem(historyBook, statusKey);
      const requestPayload = payload.request;
      const requestRecord = parseHistoryRequestRecord(requestPayload);
      if (requestRecord) {
        return {
          ...downloadItem,
          id: `history-${row.id}`,
          timestamp: dismissedAtTs || downloadItem.timestamp,
          requestId: requestRecord.id,
          requestLevel: requestRecord.request_level,
          requestNote: requestRecord.note || undefined,
          requestRecord,
          adminNote: requestRecord.admin_note || undefined,
          username: requestRecord.username || downloadItem.username,
        };
      }

      return {
        ...downloadItem,
        id: `history-${row.id}`,
        timestamp: dismissedAtTs || downloadItem.timestamp,
      };
    }

    const requestRecord = parseHistoryRequestRecord(payload.request);
    if (payload.kind === 'request' && requestRecord) {
      const requestItem = requestToActivityItem(requestRecord, viewerRole);
      return {
        ...requestItem,
        id: `history-${row.id}`,
        timestamp: dismissedAtTs || requestItem.timestamp,
      };
    }
  }

  let visualStatus: ActivityItem['visualStatus'] = 'complete';
  if (row.final_status === 'error') {
    visualStatus = 'error';
  } else if (row.final_status === 'cancelled') {
    visualStatus = 'cancelled';
  } else if (row.final_status === 'rejected') {
    visualStatus = 'rejected';
  }

  let statusLabel = 'Complete';
  if (visualStatus === 'error') {
    statusLabel = 'Failed';
  } else if (visualStatus === 'cancelled') {
    statusLabel = 'Cancelled';
  } else if (visualStatus === 'rejected') {
    statusLabel = viewerRole === 'admin' ? 'Declined' : 'Not approved';
  }

  return {
    id: `history-${row.id}`,
    kind: row.item_type === 'request' ? 'request' : 'download',
    visualStatus,
    title: row.item_type === 'request' ? 'Request' : 'Download',
    author: '',
    metaLine: row.item_key,
    statusLabel,
    timestamp: dismissedAtTs,
  };
};

interface UseActivityParams {
  isAuthenticated: boolean;
  isAdmin: boolean;
  showToast: (message: string, type?: 'info' | 'success' | 'error', persistent?: boolean) => string;
  socket: Socket | null;
}

interface UseActivityResult {
  activityStatus: StatusData;
  requestItems: ActivityItem[];
  dismissedActivityKeys: string[];
  historyItems: ActivityItem[];
  activityHistoryLoaded: boolean;
  pendingRequestCount: number;
  isActivitySnapshotLoading: boolean;
  activityHistoryLoading: boolean;
  activityHistoryHasMore: boolean;
  prefetchActivityHistory: () => void;
  refreshActivitySnapshot: () => Promise<void>;
  refreshHistoryIfLoaded: () => void;
  handleActivityTabChange: (tab: 'all' | 'downloads' | 'requests' | 'history') => void;
  resetActivity: () => void;
  handleActivityHistoryLoadMore: () => void;
  handleRequestDismiss: (requestId: number) => void;
  handleDownloadDismiss: (bookId: string, linkedRequestId?: number) => void;
  handleClearCompleted: (items: ActivityDismissTarget[]) => void;
  handleClearHistory: () => void;
}

export const useActivity = ({
  isAuthenticated,
  isAdmin,
  showToast,
  socket,
}: UseActivityParams): UseActivityResult => {
  const [activityStatus, setActivityStatus] = useState<StatusData>({});
  const [activityRequests, setActivityRequests] = useState<RequestRecord[]>([]);
  const [dismissedActivityKeys, setDismissedActivityKeys] = useState<string[]>([]);
  const [isActivitySnapshotLoading, setIsActivitySnapshotLoading] = useState(false);

  const [activityHistoryRows, setActivityHistoryRows] = useState<ActivityHistoryItem[]>([]);
  const [activityHistoryOffset, setActivityHistoryOffset] = useState(0);
  const [activityHistoryHasMore, setActivityHistoryHasMore] = useState(false);
  const [activityHistoryLoading, setActivityHistoryLoading] = useState(false);
  const [activityHistoryLoaded, setActivityHistoryLoaded] = useState(false);

  const resetActivityHistory = useCallback(() => {
    setActivityHistoryRows([]);
    setActivityHistoryOffset(0);
    setActivityHistoryHasMore(false);
    setActivityHistoryLoaded(false);
  }, []);

  const resetActivity = useCallback(() => {
    setActivityStatus({});
    setActivityRequests([]);
    setDismissedActivityKeys([]);
    resetActivityHistory();
  }, [resetActivityHistory]);

  const refreshActivitySnapshot = useCallback(async () => {
    if (!isAuthenticated) {
      resetActivity();
      return;
    }

    setIsActivitySnapshotLoading(true);
    try {
      const snapshot = await getActivitySnapshot();
      setActivityStatus(snapshot.status || {});
      setActivityRequests(Array.isArray(snapshot.requests) ? snapshot.requests : []);
      const keys = Array.isArray(snapshot.dismissed)
        ? snapshot.dismissed
            .map((entry) => entry.item_key)
            .filter((key): key is string => typeof key === 'string' && key.trim().length > 0)
        : [];
      setDismissedActivityKeys(Array.from(new Set(keys)));
    } catch (error) {
      console.warn('Failed to refresh activity snapshot:', error);
    } finally {
      setIsActivitySnapshotLoading(false);
    }
  }, [isAuthenticated, resetActivity]);

  const refreshActivityHistory = useCallback(async () => {
    if (!isAuthenticated) {
      resetActivityHistory();
      return;
    }

    setActivityHistoryLoading(true);
    try {
      const rows = await listActivityHistory(HISTORY_PAGE_SIZE, 0);
      const normalizedRows = Array.isArray(rows) ? rows : [];
      setActivityHistoryRows(normalizedRows);
      setActivityHistoryOffset(normalizedRows.length);
      setActivityHistoryHasMore(normalizedRows.length === HISTORY_PAGE_SIZE);
      setActivityHistoryLoaded(true);
    } catch (error) {
      console.warn('Failed to refresh activity history:', error);
    } finally {
      setActivityHistoryLoading(false);
    }
  }, [isAuthenticated, resetActivityHistory]);

  const handleActivityTabChange = useCallback(
    (tab: 'all' | 'downloads' | 'requests' | 'history') => {
      if (tab !== 'history' || activityHistoryLoaded || activityHistoryLoading) {
        return;
      }
      void refreshActivityHistory();
    },
    [activityHistoryLoaded, activityHistoryLoading, refreshActivityHistory],
  );

  const prefetchActivityHistory = useCallback(() => {
    if (activityHistoryLoaded || activityHistoryLoading) {
      return;
    }
    void refreshActivityHistory();
  }, [activityHistoryLoaded, activityHistoryLoading, refreshActivityHistory]);

  const handleActivityHistoryLoadMore = useCallback(() => {
    if (!isAuthenticated || activityHistoryLoading || !activityHistoryHasMore) {
      return;
    }

    setActivityHistoryLoading(true);
    void listActivityHistory(HISTORY_PAGE_SIZE, activityHistoryOffset)
      .then((rows) => {
        const normalizedRows = Array.isArray(rows) ? rows : [];
        setActivityHistoryRows((current) => {
          const existingIds = new Set(current.map((row) => row.id));
          const nextRows = normalizedRows.filter((row) => !existingIds.has(row.id));
          return [...current, ...nextRows];
        });
        setActivityHistoryOffset((current) => current + normalizedRows.length);
        setActivityHistoryHasMore(normalizedRows.length === HISTORY_PAGE_SIZE);
      })
      .catch((error) => {
        console.warn('Failed to load more activity history:', error);
      })
      .finally(() => {
        setActivityHistoryLoading(false);
      });
  }, [activityHistoryHasMore, activityHistoryLoading, activityHistoryOffset, isAuthenticated]);

  useEffect(() => {
    if (!socket || !isAuthenticated) {
      return undefined;
    }

    const refreshFromSocketEvent = () => {
      void refreshActivitySnapshot();
      if (activityHistoryLoaded) {
        void refreshActivityHistory();
      }
    };

    socket.on('activity_update', refreshFromSocketEvent);
    socket.on('request_update', refreshFromSocketEvent);
    socket.on('new_request', refreshFromSocketEvent);
    return () => {
      socket.off('activity_update', refreshFromSocketEvent);
      socket.off('request_update', refreshFromSocketEvent);
      socket.off('new_request', refreshFromSocketEvent);
    };
  }, [
    activityHistoryLoaded,
    isAuthenticated,
    refreshActivitySnapshot,
    refreshActivityHistory,
    socket,
  ]);

  const requestItems = useMemo(
    () =>
      activityRequests
        .map((record) => requestToActivityItem(record, isAdmin ? 'admin' : 'user'))
        .toSorted((left, right) => right.timestamp - left.timestamp),
    [activityRequests, isAdmin],
  );

  const historyItems = useMemo(() => {
    const mappedItems = activityHistoryRows
      .map((row) => mapHistoryRowToActivityItem(row, isAdmin ? 'admin' : 'user'))
      .toSorted((left, right) => right.timestamp - left.timestamp);

    return dedupeHistoryItems(mappedItems);
  }, [activityHistoryRows, isAdmin]);

  const pendingRequestCount = useMemo(
    () => activityRequests.filter((record) => record.status === 'pending').length,
    [activityRequests],
  );

  const refreshHistoryIfLoaded = useCallback(() => {
    if (!activityHistoryLoaded) {
      return;
    }
    void refreshActivityHistory();
  }, [activityHistoryLoaded, refreshActivityHistory]);

  const dismissItems = useCallback(
    (items: ActivityDismissPayload[], optimisticKeys: string[], errorMessage: string) => {
      setDismissedActivityKeys((current) => Array.from(new Set([...current, ...optimisticKeys])));
      void dismissManyActivityItems(items)
        .then(() => {
          void refreshActivitySnapshot();
          refreshHistoryIfLoaded();
        })
        .catch((error) => {
          console.error('Activity dismiss failed:', error);
          void refreshActivitySnapshot();
          refreshHistoryIfLoaded();
          showToast(getActivityErrorMessage(error, errorMessage), 'error');
        });
    },
    [refreshActivitySnapshot, refreshHistoryIfLoaded, showToast],
  );

  const handleRequestDismiss = useCallback(
    (requestId: number) => {
      const requestKey = `request:${requestId}`;
      setDismissedActivityKeys((current) =>
        current.includes(requestKey) ? current : [...current, requestKey],
      );

      void dismissActivityItem({
        item_type: 'request',
        item_key: requestKey,
      })
        .then(() => {
          void refreshActivitySnapshot();
          refreshHistoryIfLoaded();
        })
        .catch((error) => {
          console.error('Request dismiss failed:', error);
          void refreshActivitySnapshot();
          refreshHistoryIfLoaded();
          showToast(getActivityErrorMessage(error, 'Failed to clear request'), 'error');
        });
    },
    [refreshActivitySnapshot, refreshHistoryIfLoaded, showToast],
  );

  const handleDownloadDismiss = useCallback(
    (bookId: string, linkedRequestId?: number) => {
      const items: ActivityDismissTarget[] = [
        { itemType: 'download', itemKey: `download:${bookId}` },
      ];
      if (typeof linkedRequestId === 'number' && Number.isFinite(linkedRequestId)) {
        items.push({ itemType: 'request', itemKey: `request:${linkedRequestId}` });
      }

      dismissItems(
        items.map((item) => ({
          item_type: item.itemType,
          item_key: item.itemKey,
        })),
        items.map((item) => item.itemKey),
        'Failed to clear item',
      );
    },
    [dismissItems],
  );

  const handleClearCompleted = useCallback(
    (items: ActivityDismissTarget[]) => {
      if (!items.length) {
        return;
      }

      dismissItems(
        items.map((item) => ({
          item_type: item.itemType,
          item_key: item.itemKey,
        })),
        Array.from(new Set(items.map((item) => item.itemKey))),
        'Failed to clear finished downloads',
      );
    },
    [dismissItems],
  );

  const handleClearHistory = useCallback(() => {
    resetActivityHistory();
    void clearActivityHistory()
      .then(() => {
        void refreshActivitySnapshot();
        void refreshActivityHistory();
      })
      .catch((error) => {
        console.error('Clear history failed:', error);
        void refreshActivityHistory();
        showToast(getActivityErrorMessage(error, 'Failed to clear history'), 'error');
      });
  }, [refreshActivityHistory, refreshActivitySnapshot, resetActivityHistory, showToast]);

  return {
    activityStatus,
    requestItems,
    dismissedActivityKeys,
    historyItems,
    activityHistoryLoaded,
    pendingRequestCount,
    isActivitySnapshotLoading,
    activityHistoryLoading,
    activityHistoryHasMore,
    prefetchActivityHistory,
    refreshActivitySnapshot,
    refreshHistoryIfLoaded,
    handleActivityTabChange,
    resetActivity,
    handleActivityHistoryLoadMore,
    handleRequestDismiss,
    handleDownloadDismiss,
    handleClearCompleted,
    handleClearHistory,
  };
};

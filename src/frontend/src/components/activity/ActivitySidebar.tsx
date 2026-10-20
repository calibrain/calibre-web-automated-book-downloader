import { useCallback, useMemo, useRef, useState, type WheelEvent } from 'react';

import { useTabIndicator } from '../../hooks/ui/useTabIndicator';
import { useEscapeKey } from '../../hooks/useEscapeKey';
import { useMediaQuery } from '../../hooks/useMediaQuery';
import type { RequestRecord, StatusData } from '../../types';
import { Dropdown } from '../Dropdown';
import { ActivityCard } from './ActivityCard';
import type { DownloadStatusKey } from './activityMappers';
import { downloadToActivityItem, linkedDownloadIdForRequest } from './activityMappers';
import type { ActivityItem } from './activityTypes';
import { getClearCompletedTargets } from './clearCompleted';

interface ActivitySidebarProps {
  isOpen: boolean;
  onClose: () => void;
  status: StatusData;
  isAdmin: boolean;
  onClearCompleted: (items: ActivityDismissTarget[]) => void;
  onCancel: (id: string) => void;
  onRetry?: (id: string) => void;
  onDownloadDismiss?: (bookId: string, linkedRequestId?: number) => void;
  requestItems: ActivityItem[];
  dismissedItemKeys?: string[];
  historyItems?: ActivityItem[];
  historyLoaded?: boolean;
  historyHasMore?: boolean;
  historyLoading?: boolean;
  onHistoryLoadMore?: () => void;
  onClearHistory?: () => void;
  onActiveTabChange?: (tab: ActivityTabKey) => void;
  pendingRequestCount: number;
  showRequestsTab: boolean;
  isRequestsLoading?: boolean;
  onRequestCancel?: (requestId: number) => Promise<void> | void;
  onRequestApprove?: (
    requestId: number,
    record: RequestRecord,
    options?: {
      browseOnly?: boolean;
      manualApproval?: boolean;
    },
  ) => Promise<void> | void;
  onRequestReject?: (requestId: number, adminNote?: string) => Promise<void> | void;
  onRequestDismiss?: (requestId: number) => void;
  onPinnedOpenChange?: (pinnedOpen: boolean) => void;
  pinnedTopOffset?: number;
}

export interface ActivityDismissTarget {
  itemType: 'download' | 'request';
  itemKey: string;
}

const ACTIVITY_SIDEBAR_PINNED_STORAGE_KEY = 'activity-sidebar-pinned';

const DOWNLOAD_STATUS_KEYS: DownloadStatusKey[] = [
  'downloading',
  'locating',
  'resolving',
  'queued',
  'error',
  'complete',
  'cancelled',
];

type ActivityCategoryKey = 'needs_review' | 'in_progress' | 'complete' | 'failed';

type ActivityTabKey = 'all' | 'downloads' | 'requests' | 'history';
const ALL_USERS_FILTER = '__all_users__';

const getCategoryLabel = (key: ActivityCategoryKey, isAdmin: boolean): string => {
  if (key === 'needs_review') {
    return isAdmin ? 'Needs Review' : 'Waiting';
  }
  if (key === 'in_progress') {
    return 'In Progress';
  }
  if (key === 'complete') {
    return 'Complete';
  }
  return 'Failed';
};

const getVisibleCategoryOrder = (tab: ActivityTabKey): ActivityCategoryKey[] => {
  if (tab === 'downloads') {
    return ['in_progress', 'complete', 'failed'];
  }
  if (tab === 'requests') {
    return ['needs_review', 'in_progress', 'complete', 'failed'];
  }
  if (tab === 'history') {
    return [];
  }
  return ['needs_review', 'in_progress', 'complete', 'failed'];
};

const getActivityCategory = (item: ActivityItem): ActivityCategoryKey => {
  if (item.kind === 'download') {
    if (
      item.visualStatus === 'queued' ||
      item.visualStatus === 'resolving' ||
      item.visualStatus === 'locating' ||
      item.visualStatus === 'downloading'
    ) {
      return 'in_progress';
    }
    if (item.visualStatus === 'complete') {
      return 'complete';
    }
    return 'failed';
  }

  const requestStatus = item.requestRecord?.status;
  if (requestStatus === 'pending' || item.visualStatus === 'pending') {
    return 'needs_review';
  }

  if (requestStatus === 'rejected' || requestStatus === 'cancelled') {
    return 'failed';
  }

  const deliveryState = item.requestRecord?.delivery_state;
  if (requestStatus === 'fulfilled' || item.visualStatus === 'fulfilled') {
    if (
      deliveryState === 'queued' ||
      deliveryState === 'resolving' ||
      deliveryState === 'locating' ||
      deliveryState === 'downloading'
    ) {
      return 'in_progress';
    }
    if (deliveryState === 'error' || deliveryState === 'cancelled') {
      return 'failed';
    }
    // Legacy fulfilled requests often have unknown/none delivery state because the
    // pre-refactor queue state was ephemeral. Treat as completed approval, not in-progress.
    return 'complete';
  }

  if (deliveryState === 'complete') {
    return 'complete';
  }
  if (deliveryState === 'error' || deliveryState === 'cancelled') {
    return 'failed';
  }
  return 'in_progress';
};

const mergeRequestWithDownload = (
  requestItem: ActivityItem,
  downloadItem: ActivityItem,
): ActivityItem => {
  return {
    ...downloadItem,
    id: requestItem.id,
    kind: 'download',
    title: downloadItem.title || requestItem.title,
    author: downloadItem.author || requestItem.author,
    preview: downloadItem.preview || requestItem.preview,
    metaLine: downloadItem.metaLine,
    timestamp: Math.max(downloadItem.timestamp, requestItem.timestamp),
    username: requestItem.username || downloadItem.username,
    adminNote: requestItem.adminNote,
    requestId: requestItem.requestId,
    requestLevel: requestItem.requestLevel,
    requestNote: requestItem.requestNote,
    requestRecord: requestItem.requestRecord,
  };
};

const dedupeById = (items: ActivityItem[]): ActivityItem[] => {
  const byId = new Map<string, ActivityItem>();
  items.forEach((item) => {
    const current = byId.get(item.id);
    if (!current || item.timestamp >= current.timestamp) {
      byId.set(item.id, item);
    }
  });
  return Array.from(byId.values());
};

const getItemUsername = (item: ActivityItem): string | null => {
  const candidate = item.username || item.requestRecord?.username;
  if (typeof candidate !== 'string') {
    return null;
  }
  const normalized = candidate.trim();
  return normalized || null;
};

const parsePinned = (value: string | null): boolean => {
  if (!value) {
    return false;
  }
  return value === '1' || value.toLowerCase() === 'true';
};

const getInitialPinnedPreference = (): boolean => {
  if (typeof window === 'undefined') {
    return false;
  }
  try {
    return parsePinned(window.localStorage.getItem(ACTIVITY_SIDEBAR_PINNED_STORAGE_KEY));
  } catch {
    return false;
  }
};

const EMPTY_KEYS: string[] = [];
const EMPTY_ITEMS: ActivityItem[] = [];

export const ActivitySidebar = ({
  isOpen,
  onClose,
  status,
  isAdmin,
  onClearCompleted,
  onCancel,
  onRetry,
  onDownloadDismiss,
  requestItems,
  dismissedItemKeys = EMPTY_KEYS,
  historyItems = EMPTY_ITEMS,
  historyLoaded = false,
  historyHasMore = false,
  historyLoading = false,
  onHistoryLoadMore,
  onClearHistory,
  onActiveTabChange,
  pendingRequestCount,
  showRequestsTab,
  isRequestsLoading = false,
  onRequestCancel,
  onRequestApprove,
  onRequestReject,
  onRequestDismiss,
  onPinnedOpenChange,
  pinnedTopOffset = 0,
}: ActivitySidebarProps) => {
  const [isPinned, setIsPinned] = useState<boolean>(() => getInitialPinnedPreference());
  const isDesktop = useMediaQuery('(min-width: 1024px)');
  const [activeTab, setActiveTab] = useState<ActivityTabKey>('all');
  const [selectedUser, setSelectedUser] = useState(ALL_USERS_FILTER);
  const [rejectingRequest, setRejectingRequest] = useState<{ requestId: number } | null>(null);
  const [reviewingRequestId, setReviewingRequestId] = useState<number | null>(null);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const scrollViewportRef = useRef<HTMLDivElement | null>(null);
  const dismissedKeySet = useMemo(() => new Set(dismissedItemKeys), [dismissedItemKeys]);
  const handleTabChange = useCallback(
    (nextTab: ActivityTabKey) => {
      if (nextTab === 'downloads') {
        setRejectingRequest(null);
        setReviewingRequestId(null);
      }
      setActiveTab(nextTab);
      onActiveTabChange?.(nextTab);
    },
    [onActiveTabChange],
  );

  const isPinnedOpen = isOpen && isDesktop && isPinned;
  const effectiveActiveTab = !showRequestsTab && activeTab === 'requests' ? 'all' : activeTab;
  if (effectiveActiveTab !== activeTab) {
    setActiveTab(effectiveActiveTab);
  }

  useEscapeKey(isOpen && !isPinnedOpen, onClose);

  const downloadItems = useMemo(() => {
    const items: ActivityItem[] = [];

    DOWNLOAD_STATUS_KEYS.forEach((statusKey) => {
      const bucket = status[statusKey];
      if (!bucket) {
        return;
      }
      Object.values(bucket).forEach((book) => {
        const itemKey = `download:${book.id}`;
        const isTerminalStatus =
          statusKey === 'complete' || statusKey === 'error' || statusKey === 'cancelled';
        if (isTerminalStatus && dismissedKeySet.has(itemKey)) {
          return;
        }
        items.push(downloadToActivityItem(book, statusKey));
      });
    });

    return items.toSorted((left, right) => right.timestamp - left.timestamp);
  }, [dismissedKeySet, status]);

  const visibleRequestItems = useMemo(
    () =>
      requestItems.filter((item) => {
        if (!item.requestId) {
          return true;
        }
        return !dismissedKeySet.has(`request:${item.requestId}`);
      }),
    [dismissedKeySet, requestItems],
  );

  const { mergedRequestItems, mergedDownloadItems } = useMemo(() => {
    const downloadsById = new Map<string, ActivityItem>();
    // downloadItems is newest first, so a retried request keeps its latest download.
    const latestDownloadIdByRequestId = new Map<number, string>();
    downloadItems.forEach((item) => {
      if (item.downloadBookId) {
        downloadsById.set(item.downloadBookId, item);
        if (
          typeof item.requestId === 'number' &&
          !latestDownloadIdByRequestId.has(item.requestId)
        ) {
          latestDownloadIdByRequestId.set(item.requestId, item.downloadBookId);
        }
      }
    });

    const mergedByDownloadId = new Map<string, ActivityItem>();
    const reopenedRequestIds = new Set<number>();

    visibleRequestItems.forEach((item) => {
      if (item.kind !== 'request' || typeof item.requestId !== 'number') {
        return;
      }
      const requestRecord = item.requestRecord;
      const failureReason = requestRecord?.last_failure_reason;
      if (
        requestRecord?.status === 'pending' &&
        typeof failureReason === 'string' &&
        failureReason.trim().length > 0
      ) {
        reopenedRequestIds.add(item.requestId);
      }
    });

    const nextRequestItems = visibleRequestItems.map((requestItem) => {
      const linkedDownloadId = linkedDownloadIdForRequest(requestItem, latestDownloadIdByRequestId);
      if (!linkedDownloadId) {
        return requestItem;
      }

      const matchedDownload = downloadsById.get(linkedDownloadId);
      if (!matchedDownload) {
        return requestItem;
      }

      const merged = mergeRequestWithDownload(requestItem, matchedDownload);
      if (!mergedByDownloadId.has(linkedDownloadId)) {
        mergedByDownloadId.set(linkedDownloadId, merged);
      }
      return merged;
    });

    const nextDownloadItems = downloadItems
      .map((downloadItem) => {
        const downloadId = downloadItem.downloadBookId;
        if (!downloadId) {
          return downloadItem;
        }
        return mergedByDownloadId.get(downloadId) || downloadItem;
      })
      .filter((downloadItem) => {
        if (
          typeof downloadItem.requestId === 'number' &&
          reopenedRequestIds.has(downloadItem.requestId) &&
          (downloadItem.visualStatus === 'error' || downloadItem.visualStatus === 'cancelled')
        ) {
          return false;
        }
        return true;
      });

    return {
      mergedRequestItems: nextRequestItems,
      mergedDownloadItems: nextDownloadItems,
    };
  }, [downloadItems, visibleRequestItems]);

  const hasTerminalDownloadItems = useMemo(
    () =>
      mergedDownloadItems.some(
        (item) =>
          item.visualStatus === 'complete' ||
          item.visualStatus === 'error' ||
          item.visualStatus === 'cancelled',
      ),
    [mergedDownloadItems],
  );

  const allItems = useMemo(() => {
    const combined = dedupeById([...mergedDownloadItems, ...mergedRequestItems]);
    return combined.toSorted((a, b) => b.timestamp - a.timestamp);
  }, [mergedDownloadItems, mergedRequestItems]);

  let baseVisibleItems = mergedDownloadItems;
  if (effectiveActiveTab === 'all') {
    baseVisibleItems = allItems;
  } else if (effectiveActiveTab === 'requests') {
    baseVisibleItems = mergedRequestItems.filter((item) => {
      const requestStatus = item.requestRecord?.status;
      if (
        requestStatus === 'pending' ||
        requestStatus === 'rejected' ||
        requestStatus === 'cancelled'
      ) {
        return true;
      }
      return requestStatus === 'fulfilled' && item.kind === 'request';
    });
  } else if (effectiveActiveTab === 'history') {
    baseVisibleItems = historyItems;
  }
  const isHistoryInitialLoad = effectiveActiveTab === 'history' && !historyLoaded;
  let emptyStateMessage = 'No activity';
  if (effectiveActiveTab === 'requests') {
    emptyStateMessage = isRequestsLoading ? 'Loading requests...' : 'No requests';
  } else if (effectiveActiveTab === 'history') {
    emptyStateMessage =
      historyLoading || isHistoryInitialLoad ? 'Loading history...' : 'No history';
  } else if (effectiveActiveTab === 'downloads') {
    emptyStateMessage = 'No downloads';
  }

  const availableUsers = useMemo(() => {
    const userMap = new Map<string, string>();
    baseVisibleItems.forEach((item) => {
      const username = getItemUsername(item);
      if (!username) {
        return;
      }
      const lookupKey = username.toLowerCase();
      if (!userMap.has(lookupKey)) {
        userMap.set(lookupKey, username);
      }
    });

    return Array.from(userMap.values()).toSorted((left, right) => left.localeCompare(right));
  }, [baseVisibleItems]);

  const effectiveSelectedUser =
    selectedUser === ALL_USERS_FILTER || availableUsers.includes(selectedUser)
      ? selectedUser
      : ALL_USERS_FILTER;
  if (effectiveSelectedUser !== selectedUser) {
    setSelectedUser(ALL_USERS_FILTER);
  }

  const visibleItems = useMemo(() => {
    if (effectiveSelectedUser === ALL_USERS_FILTER) {
      return baseVisibleItems;
    }
    return baseVisibleItems.filter((item) => getItemUsername(item) === effectiveSelectedUser);
  }, [baseVisibleItems, effectiveSelectedUser]);

  const visiblePendingRequestIds = useMemo(() => {
    const ids = new Set<number>();
    visibleItems.forEach((item) => {
      if (
        item.kind === 'request' &&
        item.requestRecord?.status === 'pending' &&
        typeof item.requestId === 'number'
      ) {
        ids.add(item.requestId);
      }
    });
    return ids;
  }, [visibleItems]);

  const effectiveReviewingRequestId =
    reviewingRequestId !== null && visiblePendingRequestIds.has(reviewingRequestId)
      ? reviewingRequestId
      : null;
  const effectiveRejectingRequest =
    rejectingRequest !== null && visiblePendingRequestIds.has(rejectingRequest.requestId)
      ? rejectingRequest
      : null;
  if (effectiveReviewingRequestId !== reviewingRequestId) {
    setReviewingRequestId(null);
  }
  if (effectiveRejectingRequest === null && rejectingRequest !== null) {
    setRejectingRequest(null);
  }

  const hasUserFilter = isAdmin && availableUsers.length > 1;

  const clearCompletedTargets = useMemo(
    () => getClearCompletedTargets(visibleItems, visibleRequestItems),
    [visibleItems, visibleRequestItems],
  );

  const visibleCategoryOrder = useMemo(
    () => getVisibleCategoryOrder(effectiveActiveTab),
    [effectiveActiveTab],
  );

  const groupedVisibleItems = useMemo(() => {
    if (effectiveActiveTab === 'history') {
      return [];
    }

    const grouped = new Map<ActivityCategoryKey, ActivityItem[]>();
    visibleCategoryOrder.forEach((key) => grouped.set(key, []));

    visibleItems.forEach((item) => {
      const category = getActivityCategory(item);
      if (!grouped.has(category)) {
        grouped.set(category, []);
      }
      const bucket = grouped.get(category);
      if (bucket) {
        bucket.push(item);
      }
    });

    return visibleCategoryOrder
      .map((key) => ({
        key,
        label: getCategoryLabel(key, isAdmin),
        items: (grouped.get(key) ?? []).toSorted((left, right) => right.timestamp - left.timestamp),
      }))
      .filter((group) => group.items.length > 0);
  }, [effectiveActiveTab, isAdmin, visibleItems, visibleCategoryOrder]);

  const handleTogglePinned = () => {
    const next = !isPinned;
    setIsPinned(next);
    onPinnedOpenChange?.(next);
    try {
      window.localStorage.setItem(ACTIVITY_SIDEBAR_PINNED_STORAGE_KEY, next ? '1' : '0');
    } catch {
      // Ignore storage failures
    }
  };

  // Tab indicator (sliding underline, same pattern as ReleaseModal)
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const tabIndicatorStyle = useTabIndicator(tabRefs, effectiveActiveTab, showRequestsTab);

  const panel = (
    <>
      <div
        className="px-4 pt-4 pb-0"
        style={{
          borderColor: 'var(--border-muted)',
          paddingTop: 'calc(1rem + env(safe-area-inset-top))',
        }}
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold">
              {effectiveActiveTab === 'history' ? 'History' : 'Activity'}
            </h2>
            <button
              type="button"
              onClick={handleTogglePinned}
              className="hover-action hidden h-9 w-9 items-center justify-center rounded-full transition-colors lg:inline-flex"
              title={isPinned ? 'Unpin activity sidebar' : 'Pin activity sidebar'}
              aria-label={isPinned ? 'Unpin activity sidebar' : 'Pin activity sidebar'}
            >
              {isPinned ? (
                <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <path d="M15.804 2.276a.75.75 0 0 0-.336.195l-2 2a.75.75 0 0 0 0 1.062l.47.469-3.572 3.571c-.83-.534-1.773-.808-2.709-.691-1.183.148-2.32.72-3.187 1.587a.75.75 0 0 0 0 1.063L7.938 15l-5.467 5.467a.75.75 0 0 0 0 1.062.75.75 0 0 0 1.062 0L9 16.062l3.468 3.468a.75.75 0 0 0 1.062 0c.868-.868 1.44-2.004 1.588-3.187.117-.935-.158-1.879-.692-2.708L18 10.063l.469.469a.75.75 0 0 0 1.062 0l2-2a.75.75 0 0 0 0-1.062l-5-4.999a.75.75 0 0 0-.726-.195z" />
                </svg>
              ) : (
                <svg
                  className="h-4 w-4"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.75"
                  aria-hidden="true"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="m9 15-6 6M15 6l-1-1 2-2 5 5-2 2-1-1-4.5 4.5c1.5 1.5 1 4-.5 5.5l-8-8c1.5-1.5 4-2 5.5-.5z"
                  />
                </svg>
              )}
            </button>
          </div>

          <div className="flex items-center gap-1">
            {hasUserFilter && (
              <Dropdown
                align="right"
                widthClassName="w-auto"
                panelClassName="min-w-44"
                renderTrigger={({ isOpen: isDropdownOpen, toggle }) => (
                  <button
                    type="button"
                    onClick={toggle}
                    className={`hover-action inline-flex h-9 w-9 items-center justify-center rounded-full transition-colors ${
                      isDropdownOpen || effectiveSelectedUser !== ALL_USERS_FILTER
                        ? 'text-sky-600 dark:text-sky-400'
                        : ''
                    }`}
                    title={
                      effectiveSelectedUser === ALL_USERS_FILTER
                        ? 'Filter by user'
                        : `Filtered: ${effectiveSelectedUser}`
                    }
                    aria-label={
                      effectiveSelectedUser === ALL_USERS_FILTER
                        ? 'Filter by user'
                        : `Filtered by user ${effectiveSelectedUser}`
                    }
                    aria-expanded={isDropdownOpen}
                  >
                    <svg
                      className="h-5 w-5"
                      viewBox="0 0 24 24"
                      fill="none"
                      strokeWidth="1.75"
                      stroke="currentColor"
                      aria-hidden="true"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 0 1-.659 1.591l-5.432 5.432a2.25 2.25 0 0 0-.659 1.591v2.927a2.25 2.25 0 0 1-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 0 0-.659-1.591L3.659 7.409A2.25 2.25 0 0 1 3 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0 1 12 3Z"
                      />
                    </svg>
                  </button>
                )}
              >
                {({ close }) => (
                  <div role="listbox">
                    {[ALL_USERS_FILTER, ...availableUsers].map((value) => {
                      const isSelected = effectiveSelectedUser === value;
                      const label = value === ALL_USERS_FILTER ? 'All users' : value;
                      return (
                        <button
                          type="button"
                          key={value}
                          className={`hover-surface flex w-full items-center justify-between px-3 py-2 text-left text-sm ${
                            isSelected ? 'text-sky-600 dark:text-sky-400' : ''
                          }`}
                          onClick={() => {
                            setSelectedUser(value);
                            close();
                          }}
                        >
                          <span>{label}</span>
                          {isSelected && (
                            <svg
                              className="h-4 w-4"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                            >
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                d="m5 13 4 4L19 7"
                              />
                            </svg>
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
              </Dropdown>
            )}
            <button
              type="button"
              onClick={() => handleTabChange(effectiveActiveTab === 'history' ? 'all' : 'history')}
              className={`hover-action relative inline-flex h-9 w-9 items-center justify-center rounded-full transition-colors ${
                effectiveActiveTab === 'history' ? 'text-sky-600 dark:text-sky-400' : ''
              }`}
              title={effectiveActiveTab === 'history' ? 'Back to activity' : 'Open history'}
              aria-label={effectiveActiveTab === 'history' ? 'Back to activity' : 'Open history'}
              aria-pressed={effectiveActiveTab === 'history'}
            >
              <svg
                className="h-5 w-5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.75"
                aria-hidden="true"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6l3.75 2.25" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 3v4.5h4.5" />
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M3.75 12a8.25 8.25 0 1 0 3.37-6.63"
                />
              </svg>
            </button>
            <button
              type="button"
              onClick={onClose}
              className="hover-action inline-flex h-9 w-9 items-center justify-center rounded-full transition-colors"
              aria-label="Close activity sidebar"
            >
              <svg
                className="h-5 w-5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.75"
                aria-hidden="true"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {effectiveActiveTab !== 'history' && (
          <div className="-mx-4 mt-2 border-b border-(--border-muted) px-4">
            <div className="relative flex gap-1">
              {/* Sliding indicator */}
              <div
                className="absolute bottom-0 h-0.5 bg-sky-500 transition-all duration-300 ease-out"
                style={{
                  left: tabIndicatorStyle.left,
                  width: tabIndicatorStyle.width,
                }}
              />
              <button
                type="button"
                ref={(el) => {
                  tabRefs.current.all = el;
                }}
                onClick={() => handleTabChange('all')}
                className={`border-b-2 border-transparent px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-colors ${
                  effectiveActiveTab === 'all'
                    ? 'text-sky-600 dark:text-sky-400'
                    : 'text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-200'
                }`}
                aria-current={effectiveActiveTab === 'all' ? 'page' : undefined}
              >
                All
              </button>
              <button
                type="button"
                ref={(el) => {
                  tabRefs.current.downloads = el;
                }}
                onClick={() => handleTabChange('downloads')}
                className={`border-b-2 border-transparent px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-colors ${
                  effectiveActiveTab === 'downloads'
                    ? 'text-sky-600 dark:text-sky-400'
                    : 'text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-200'
                }`}
                aria-current={effectiveActiveTab === 'downloads' ? 'page' : undefined}
              >
                Downloads
                {mergedDownloadItems.length > 0 && (
                  <span className="ml-1.5 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-sky-500/15 px-1 text-[11px] leading-none text-sky-700 dark:text-sky-300">
                    {mergedDownloadItems.length}
                  </span>
                )}
              </button>
              {showRequestsTab && (
                <button
                  type="button"
                  ref={(el) => {
                    tabRefs.current.requests = el;
                  }}
                  onClick={() => handleTabChange('requests')}
                  className={`border-b-2 border-transparent px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-colors ${
                    effectiveActiveTab === 'requests'
                      ? 'text-sky-600 dark:text-sky-400'
                      : 'text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-200'
                  }`}
                  aria-current={effectiveActiveTab === 'requests' ? 'page' : undefined}
                >
                  Requests
                  {pendingRequestCount > 0 && (
                    <span className="ml-1.5 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-amber-500/15 px-1 text-[11px] leading-none text-amber-700 dark:text-amber-300">
                      {pendingRequestCount}
                    </span>
                  )}
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      <div
        ref={scrollViewportRef}
        className="flex-1 overflow-y-auto overscroll-y-contain p-4"
        style={{ paddingBottom: 'calc(1rem + env(safe-area-inset-bottom))' }}
      >
        {(() => {
          if (visibleItems.length === 0) {
            return <p className="mt-8 text-center text-sm opacity-70">{emptyStateMessage}</p>;
          }

          if (effectiveActiveTab === 'history') {
            return (
              <div className="divide-y divide-[color-mix(in_srgb,var(--border-muted)_60%,transparent)]">
                {visibleItems.map((item) => (
                  <ActivityCard key={item.id} item={item} isAdmin={isAdmin} />
                ))}
                {historyHasMore && (
                  <div className="pt-3 text-center">
                    <button
                      type="button"
                      onClick={() => onHistoryLoadMore?.()}
                      disabled={historyLoading}
                      className="text-sm text-sky-600 hover:underline disabled:opacity-60 dark:text-sky-400"
                    >
                      {historyLoading ? 'Loading...' : 'Load more'}
                    </button>
                  </div>
                )}
              </div>
            );
          }

          return groupedVisibleItems.map((group) => (
            <section key={group.key} className="mb-4 last:mb-0">
              {effectiveActiveTab !== 'downloads' && (
                <button
                  type="button"
                  onClick={() =>
                    setCollapsedGroups((prev) => ({ ...prev, [group.key]: !prev[group.key] }))
                  }
                  className="mb-2 flex w-full cursor-pointer items-center justify-between text-[11px] tracking-wide uppercase opacity-70 transition-opacity hover:opacity-100"
                >
                  <div className="flex items-center gap-1.5">
                    <svg
                      className={`h-3 w-3 transition-transform ${collapsedGroups[group.key] ? '-rotate-90' : ''}`}
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                      strokeWidth="1.5"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="m19.5 8.25-7.5 7.5-7.5-7.5"
                      />
                    </svg>
                    <span>{group.label}</span>
                  </div>
                  <span className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-gray-500/10 px-1 leading-none dark:bg-gray-400/10">
                    {group.items.length}
                  </span>
                </button>
              )}
              {!collapsedGroups[group.key] && (
                <div className="divide-y divide-[color-mix(in_srgb,var(--border-muted)_60%,transparent)]">
                  {group.items.map((item) => {
                    const showRequestActions =
                      effectiveActiveTab === 'requests' || effectiveActiveTab === 'all';
                    const requestId = item.requestId;
                    const shouldShowRejectDialog =
                      showRequestActions &&
                      effectiveRejectingRequest !== null &&
                      requestId === effectiveRejectingRequest.requestId;
                    const requestRecord = item.requestRecord;
                    const canShowRequestReview =
                      showRequestActions &&
                      isAdmin &&
                      item.kind === 'request' &&
                      typeof requestId === 'number' &&
                      requestRecord?.status === 'pending';
                    const shouldShowRequestReview =
                      canShowRequestReview &&
                      effectiveReviewingRequestId !== null &&
                      requestId === effectiveReviewingRequestId &&
                      requestRecord !== undefined;

                    return (
                      <div key={item.id}>
                        <ActivityCard
                          item={item}
                          isAdmin={isAdmin}
                          onDownloadCancel={onCancel}
                          onDownloadRetry={onRetry}
                          onDownloadDismiss={onDownloadDismiss}
                          onRequestCancel={
                            onRequestCancel
                              ? (nextRequestId) => {
                                  void onRequestCancel(nextRequestId);
                                }
                              : undefined
                          }
                          onRequestApprove={onRequestApprove}
                          onRequestDismiss={onRequestDismiss}
                          onRequestReject={
                            showRequestActions && onRequestReject
                              ? (nextRequestId) => {
                                  setReviewingRequestId(null);
                                  setRejectingRequest({ requestId: nextRequestId });
                                }
                              : undefined
                          }
                          showRequestDetailsToggle={canShowRequestReview}
                          isRequestDetailsOpen={shouldShowRequestReview}
                          isSelected={shouldShowRequestReview || shouldShowRejectDialog}
                          onRequestReviewApprove={
                            onRequestApprove
                              ? async (approvedRequestId, record, options) => {
                                  await onRequestApprove(approvedRequestId, record, options);
                                  setReviewingRequestId(null);
                                }
                              : undefined
                          }
                          isRequestRejectOpen={shouldShowRejectDialog}
                          onRequestRejectClose={() => setRejectingRequest(null)}
                          onRequestRejectConfirm={
                            onRequestReject
                              ? async (rejectedRequestId, adminNote) => {
                                  await onRequestReject(rejectedRequestId, adminNote);
                                  setRejectingRequest(null);
                                }
                              : undefined
                          }
                          onRequestDetailsToggle={
                            canShowRequestReview && typeof requestId === 'number'
                              ? () => {
                                  if (shouldShowRejectDialog) {
                                    setRejectingRequest(null);
                                    return;
                                  }
                                  setRejectingRequest(null);
                                  setReviewingRequestId((current) =>
                                    current === requestId ? null : requestId,
                                  );
                                }
                              : undefined
                          }
                          onRequestDetailsOpen={
                            canShowRequestReview && typeof requestId === 'number'
                              ? () => {
                                  setRejectingRequest(null);
                                  setReviewingRequestId(requestId);
                                }
                              : undefined
                          }
                        />
                      </div>
                    );
                  })}
                </div>
              )}
            </section>
          ));
        })()}
      </div>

      {(effectiveActiveTab === 'downloads' || effectiveActiveTab === 'all') &&
        hasTerminalDownloadItems &&
        clearCompletedTargets.length > 0 && (
          <div
            className="flex items-center justify-center border-t p-3"
            style={{
              borderColor: 'var(--border-muted)',
              paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom))',
            }}
          >
            <button
              type="button"
              onClick={() => onClearCompleted(clearCompletedTargets)}
              className="text-sm text-gray-500 transition-colors hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
            >
              Clear Completed
            </button>
          </div>
        )}

      {effectiveActiveTab === 'history' && historyItems.length > 0 && (
        <div
          className="flex items-center justify-center border-t p-3"
          style={{
            borderColor: 'var(--border-muted)',
            paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom))',
          }}
        >
          <button
            type="button"
            onClick={onClearHistory}
            className="text-sm text-gray-500 transition-colors hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
          >
            Clear History
          </button>
        </div>
      )}
    </>
  );

  if (isPinnedOpen) {
    const handlePinnedWheel = (event: WheelEvent<HTMLElement>) => {
      const viewport = scrollViewportRef.current;
      if (!viewport) {
        return;
      }
      // Keep wheel/trackpad scrolling contained to the pinned activity panel.
      event.preventDefault();
      event.stopPropagation();
      viewport.scrollTop += event.deltaY;
    };

    return (
      <aside
        className="fixed right-0 z-30 hidden w-96 flex-col overflow-hidden rounded-2xl bg-(--bg-soft) shadow-lg lg:flex"
        style={{
          top: `${pinnedTopOffset}px`,
          height: `calc(100dvh - ${pinnedTopOffset}px - 0.75rem)`,
          right: '0.75rem',
        }}
        onWheel={handlePinnedWheel}
        aria-hidden={!isOpen}
      >
        {panel}
      </aside>
    );
  }

  return (
    <>
      <button
        type="button"
        className={`fixed inset-0 z-45 bg-black/50 transition-opacity duration-300 ${
          isOpen ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
        onClick={onClose}
        aria-label="Close activity sidebar"
        tabIndex={-1}
      />

      <aside
        className={`fixed top-0 right-0 z-50 flex h-full w-full flex-col shadow-2xl transition-transform duration-300 sm:w-96 ${
          isOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
        style={{ background: 'var(--bg)' }}
        aria-hidden={!isOpen}
      >
        {panel}
      </aside>
    </>
  );
};

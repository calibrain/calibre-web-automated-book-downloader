import type { ReactNode } from 'react';
import { useLayoutEffect, useMemo, useRef, useState } from 'react';

import type { RequestRecord } from '../../types';
import { withBasePath } from '../../utils/basePath';
import { Tooltip } from '../shared/Tooltip';
import type { ActivityCardAction } from './activityCardModel';
import { buildActivityCardModel } from './activityCardModel';
import { STATUS_BADGE_STYLES, STATUS_TOOLTIP_CLASSES, getProgressConfig } from './activityStyles';
import type { ActivityItem } from './activityTypes';

interface RequestApproveOptions {
  browseOnly?: boolean;
  manualApproval?: boolean;
}

type RequestApproveHandler = (
  requestId: number,
  record: RequestRecord,
  options?: RequestApproveOptions,
) => Promise<void> | void;

interface ActivityCardProps {
  item: ActivityItem;
  isAdmin: boolean;
  onDownloadCancel?: (bookId: string) => void;
  onDownloadRetry?: (bookId: string) => void;
  onDownloadDismiss?: (bookId: string, linkedRequestId?: number) => void;
  onRequestCancel?: (requestId: number) => void;
  onRequestApprove?: RequestApproveHandler;
  onRequestReviewApprove?: RequestApproveHandler;
  onRequestReject?: (requestId: number, adminNote?: string) => Promise<void> | void;
  onRequestRejectConfirm?: (requestId: number, adminNote?: string) => Promise<void> | void;
  onRequestReopen?: (requestId: number) => Promise<void> | void;
  onRequestDismiss?: (requestId: number) => void;
  showRequestDetailsToggle?: boolean;
  isRequestDetailsOpen?: boolean;
  onRequestDetailsToggle?: () => void;
  onRequestDetailsOpen?: () => void;
  isRequestRejectOpen?: boolean;
  onRequestRejectClose?: () => void;
  isSelected?: boolean;
}

const BookFallback = () => (
  <div className="flex h-18 w-12 items-center justify-center rounded-sm bg-gray-200 text-[8px] font-medium text-gray-500 dark:bg-gray-700 dark:text-gray-400">
    No Cover
  </div>
);

const IconButton = ({
  title,
  className,
  onClick,
  children,
}: {
  title: string;
  className: string;
  onClick: () => void;
  children: ReactNode;
}) => (
  <button
    type="button"
    onClick={onClick}
    aria-label={title}
    className={`inline-flex h-7 w-7 items-center justify-center rounded-full transition-colors ${className}`}
  >
    {children}
  </button>
);

const actionKey = (action: ActivityCardAction): string => {
  switch (action.kind) {
    case 'download-remove':
    case 'download-stop':
    case 'download-retry':
    case 'download-dismiss':
      return `${action.kind}-${action.bookId}`;
    case 'request-approve':
      return `${action.kind}-${action.requestId}-${action.record.id}`;
    case 'request-reject':
    case 'request-reopen':
    case 'request-cancel':
    case 'request-dismiss':
      return `${action.kind}-${action.requestId}`;
    default:
      return 'action';
  }
};

const actionUiConfig = (
  action: ActivityCardAction,
): { title: string; className: string; icon: 'cross' | 'check' | 'stop' | 'retry' } => {
  switch (action.kind) {
    case 'download-remove':
      return {
        title: 'Remove from queue',
        className: 'text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30',
        icon: 'cross',
      };
    case 'download-stop':
      return {
        title: 'Stop download',
        className: 'text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30',
        icon: 'stop',
      };
    case 'download-dismiss':
      return {
        title: 'Clear',
        className: 'text-gray-500 hover:text-red-600 hover:bg-red-100 dark:hover:bg-red-900/30',
        icon: 'cross',
      };
    case 'download-retry':
      return {
        title: 'Retry',
        className: 'text-sky-600 dark:text-sky-400 hover:bg-sky-100 dark:hover:bg-sky-900/30',
        icon: 'retry',
      };
    case 'request-approve':
      return {
        title: 'Approve',
        className:
          'text-green-600 dark:text-green-400 hover:bg-green-100 dark:hover:bg-green-900/30',
        icon: 'check',
      };
    case 'request-reject':
      return {
        title: 'Reject',
        className: 'text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30',
        icon: 'cross',
      };
    case 'request-reopen':
      return {
        title: 'Reopen request',
        className: 'text-sky-600 dark:text-sky-400 hover:bg-sky-100 dark:hover:bg-sky-900/30',
        icon: 'retry',
      };
    case 'request-cancel':
      return {
        title: 'Cancel request',
        className: 'text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/30',
        icon: 'cross',
      };
    case 'request-dismiss':
      return {
        title: 'Clear',
        className: 'text-gray-500 hover:text-red-600 hover:bg-red-100 dark:hover:bg-red-900/30',
        icon: 'cross',
      };
    default:
      return {
        title: 'Action',
        className: 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700',
        icon: 'cross',
      };
  }
};

const ActionIcon = ({ icon }: { icon: 'cross' | 'check' | 'stop' | 'retry' }) => {
  if (icon === 'stop') {
    return (
      <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
        <rect x="6" y="6" width="12" height="12" rx="2" />
      </svg>
    );
  }
  if (icon === 'check') {
    return (
      <svg
        className="h-4 w-4"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.25"
        aria-hidden="true"
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="m5 13 4 4L19 7" />
      </svg>
    );
  }
  if (icon === 'retry') {
    return (
      <svg
        className="h-4 w-4"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        aria-hidden="true"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M18.363 5.634A8.997 9.002 29.494 0 0 7.5 4.206 8.997 9.002 29.494 0 0 3.306 14.33 8.997 9.002 29.494 0 0 11.996 21a8.997 9.002 29.494 0 0 8.694-6.673m-2.327-8.693L20.87 8.14m.017-4.994v5.015m0 0h-5.013"
        />
      </svg>
    );
  }
  return (
    <svg
      className="h-4 w-4"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.25"
      aria-hidden="true"
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
    </svg>
  );
};

const asRecord = (value: Record<string, unknown> | null | undefined): Record<string, unknown> =>
  value ?? {};

const toOptionalText = (value: unknown): string | undefined => {
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value);
  }
  return undefined;
};

const toSourceLabel = (value: unknown): string => {
  const text = toOptionalText(value);
  if (!text) {
    return 'Any Source';
  }
  const normalized = text.trim().toLowerCase();
  if (normalized === '*' || normalized === 'any' || normalized === 'all') {
    return 'Any Source';
  }
  return text
    .split(/[_\s-]+/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
};

const formatDateTime = (isoDate: string): string => {
  const parsed = Date.parse(isoDate);
  if (!Number.isFinite(parsed)) {
    return isoDate;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed);
};

const hasAttachedReleaseData = (record: RequestRecord): boolean => {
  if (record.request_level !== 'release') {
    return false;
  }
  if (!record.release_data || typeof record.release_data !== 'object') {
    return false;
  }
  return Object.keys(record.release_data).length > 0;
};

const DetailField = ({ label, value }: { label: string; value: string }) => (
  <div className="py-1">
    <p className="text-[10px] tracking-wide uppercase opacity-60">{label}</p>
    <p className="mt-0.5 text-xs font-medium wrap-break-word">{value}</p>
  </div>
);

interface ReviewInlinePanelProps {
  reviewRecord: RequestRecord;
  showSourceField: boolean;
  requestedAt: string;
  requestType: string;
  sourceLabel: string;
  hasAttachedRelease: boolean;
  missingAttachedReleaseMessage: string;
  approveLabel: string;
  canMarkAsApprovedWithoutRelease: boolean;
  canBrowseAlternatives: boolean;
  fileTitle: string;
  fileSize: string;
  fileFormat: string;
  requiresBrowseBeforeApprove: boolean;
  reviewApproveHandler: RequestApproveHandler;
}

const ReviewInlinePanel = ({
  reviewRecord,
  showSourceField,
  requestedAt,
  requestType,
  sourceLabel,
  hasAttachedRelease,
  missingAttachedReleaseMessage,
  approveLabel,
  canMarkAsApprovedWithoutRelease,
  canBrowseAlternatives,
  fileTitle,
  fileSize,
  fileFormat,
  requiresBrowseBeforeApprove,
  reviewApproveHandler,
}: ReviewInlinePanelProps) => {
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleReviewApprove = async () => {
    if (isSubmitting) {
      return;
    }

    setIsSubmitting(true);
    try {
      if (requiresBrowseBeforeApprove) {
        await reviewApproveHandler(reviewRecord.id, reviewRecord, { browseOnly: true });
        return;
      }

      await reviewApproveHandler(reviewRecord.id, reviewRecord);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleReviewBrowseAlternatives = async () => {
    if (isSubmitting) {
      return;
    }

    setIsSubmitting(true);
    try {
      await reviewApproveHandler(reviewRecord.id, reviewRecord, { browseOnly: true });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleReviewManualApproval = async () => {
    if (isSubmitting) {
      return;
    }

    setIsSubmitting(true);
    try {
      await reviewApproveHandler(reviewRecord.id, reviewRecord, { manualApproval: true });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="animate-fade-in -mx-4 mt-2 space-y-3 px-4 pb-2">
      <div
        className={`grid grid-cols-1 ${showSourceField ? 'sm:grid-cols-3' : 'sm:grid-cols-2'} gap-x-3 gap-y-1`}
      >
        <DetailField label="Requested" value={requestedAt} />
        <DetailField label="Type" value={requestType} />
        {showSourceField && <DetailField label="Source" value={sourceLabel} />}
      </div>

      {hasAttachedRelease ? (
        <div className="space-y-2">
          <p className="text-[11px] font-medium tracking-wide uppercase opacity-70">
            Attached File
          </p>
          <div className="grid grid-cols-1 gap-x-3 gap-y-1">
            <DetailField label="Title" value={fileTitle} />
          </div>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1">
            <DetailField label="Size" value={fileSize} />
            <DetailField label="Format" value={fileFormat.toUpperCase()} />
          </div>
        </div>
      ) : (
        <p className="text-xs opacity-70">{missingAttachedReleaseMessage}</p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void handleReviewApprove()}
          disabled={isSubmitting}
          className="rounded-md bg-green-600 px-2.5 py-1.5 text-xs font-medium text-white transition-colors hover:bg-green-700 disabled:opacity-60"
        >
          {isSubmitting ? 'Working...' : approveLabel}
        </button>
        {canMarkAsApprovedWithoutRelease && (
          <button
            type="button"
            onClick={() => void handleReviewManualApproval()}
            disabled={isSubmitting}
            className="rounded-md border border-(--border-muted) px-2.5 py-1.5 text-xs transition-colors hover:bg-(--hover-surface) disabled:opacity-50"
          >
            {isSubmitting ? 'Working...' : 'Manually Mark as Approved'}
          </button>
        )}
        {canBrowseAlternatives && hasAttachedRelease && (
          <button
            type="button"
            onClick={() => void handleReviewBrowseAlternatives()}
            disabled={isSubmitting}
            className="rounded-md border border-(--border-muted) px-2.5 py-1.5 text-xs transition-colors hover:bg-(--hover-surface) disabled:opacity-50"
          >
            Browse Alternatives
          </button>
        )}
      </div>
    </div>
  );
};

interface RejectInlinePanelProps {
  requestId: number;
  itemTitle: string;
  onRequestRejectClose?: () => void;
  onRequestRejectConfirm: (requestId: number, adminNote?: string) => Promise<void> | void;
}

const RejectInlinePanel = ({
  requestId,
  itemTitle,
  onRequestRejectClose,
  onRequestRejectConfirm,
}: RejectInlinePanelProps) => {
  const [rejectNote, setRejectNote] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleInlineRejectConfirm = async () => {
    if (isSubmitting) {
      return;
    }

    setIsSubmitting(true);
    try {
      const trimmed = rejectNote.trim();
      await onRequestRejectConfirm(requestId, trimmed || undefined);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="animate-fade-in -mx-4 mt-2 space-y-3 px-4 pb-2">
      <p className="text-xs font-medium">
        Reject request for <span className="opacity-80">{itemTitle || 'Untitled request'}</span>
      </p>
      <textarea
        aria-label="Optional note shown to the user"
        value={rejectNote}
        onChange={(event) => setRejectNote(event.target.value.slice(0, MAX_ADMIN_NOTE_LENGTH))}
        rows={3}
        maxLength={MAX_ADMIN_NOTE_LENGTH}
        placeholder="Optional note shown to the user"
        className="min-h-[72px] w-full resize-y rounded-md border border-(--border-muted) bg-(--bg) px-2.5 py-2 text-xs focus:border-red-500 focus:ring-2 focus:ring-red-500/30 focus:outline-hidden"
        disabled={isSubmitting}
      />
      <div className="flex items-center justify-between">
        <span className="text-[11px] opacity-60">
          {rejectNote.length}/{MAX_ADMIN_NOTE_LENGTH}
        </span>
        <div className="inline-flex items-center gap-2">
          <button
            type="button"
            onClick={onRequestRejectClose}
            disabled={isSubmitting}
            className="rounded-md px-2.5 py-1.5 text-xs transition-colors hover:bg-(--hover-surface) disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void handleInlineRejectConfirm()}
            disabled={isSubmitting}
            className="rounded-md bg-red-600 px-2.5 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-60"
          >
            {isSubmitting ? 'Rejecting...' : 'Reject'}
          </button>
        </div>
      </div>
    </div>
  );
};

const MAX_ADMIN_NOTE_LENGTH = 1000;

export const ActivityCard = ({
  item,
  isAdmin,
  onDownloadCancel,
  onDownloadRetry,
  onDownloadDismiss,
  onRequestCancel,
  onRequestApprove,
  onRequestReviewApprove,
  onRequestReject,
  onRequestRejectConfirm,
  onRequestReopen,
  onRequestDismiss,
  showRequestDetailsToggle = false,
  isRequestDetailsOpen = false,
  onRequestDetailsToggle,
  onRequestDetailsOpen,
  isRequestRejectOpen = false,
  onRequestRejectClose,
  isSelected = false,
}: ActivityCardProps) => {
  const model = useMemo(() => buildActivityCardModel(item, isAdmin), [item, isAdmin]);
  const noteLine = model.noteLine;
  const badgeRefs = useRef<Record<string, HTMLSpanElement | null>>({});
  const titleLineRef = useRef<HTMLParagraphElement | null>(null);
  const [badgeOverflow, setBadgeOverflow] = useState<Record<string, boolean>>({});
  const [titleOverflow, setTitleOverflow] = useState(false);

  useLayoutEffect(() => {
    const measureBadgeOverflow = () => {
      const nextOverflow: Record<string, boolean> = {};
      model.badges.forEach((badge, index) => {
        const badgeId = `${badge.key}-${index}`;
        const element = badgeRefs.current[badgeId];
        nextOverflow[badgeId] = Boolean(element && element.scrollWidth - element.clientWidth > 1);
      });

      setBadgeOverflow((current) => {
        const currentKeys = Object.keys(current);
        const nextKeys = Object.keys(nextOverflow);
        if (
          currentKeys.length === nextKeys.length &&
          nextKeys.every((key) => current[key] === nextOverflow[key])
        ) {
          return current;
        }
        return nextOverflow;
      });
    };

    measureBadgeOverflow();

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measureBadgeOverflow);
      return () => window.removeEventListener('resize', measureBadgeOverflow);
    }

    const observer = new ResizeObserver(measureBadgeOverflow);
    model.badges.forEach((badge, index) => {
      const badgeId = `${badge.key}-${index}`;
      const element = badgeRefs.current[badgeId];
      if (element) {
        observer.observe(element);
      }
    });

    return () => observer.disconnect();
  }, [model.badges]);

  useLayoutEffect(() => {
    const measureTitleOverflow = () => {
      const element = titleLineRef.current;
      const nextOverflow = Boolean(element && element.scrollWidth - element.clientWidth > 1);
      setTitleOverflow((current) => (current === nextOverflow ? current : nextOverflow));
    };

    measureTitleOverflow();

    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measureTitleOverflow);
      return () => window.removeEventListener('resize', measureTitleOverflow);
    }

    const observer = new ResizeObserver(measureTitleOverflow);
    if (titleLineRef.current) {
      observer.observe(titleLineRef.current);
    }

    return () => observer.disconnect();
    // None of these are read here - they are all re-measure triggers. The title's overflow
    // depends on its text and on the width it is laid out in, and opening either panel
    // reflows the card. Drop them and the tooltip-on-truncation goes stale.
    // oxlint-disable-next-line react/exhaustive-effect-dependencies
  }, [item.title, item.author, isRequestDetailsOpen, isRequestRejectOpen]);

  const reviewRecord = item.requestRecord;
  const reviewApproveHandler = onRequestReviewApprove || onRequestApprove;
  const isDetailsExpanded = isRequestDetailsOpen || isRequestRejectOpen;

  const runAction = (action: ActivityCardAction) => {
    switch (action.kind) {
      case 'download-remove':
      case 'download-stop':
        onDownloadCancel?.(action.bookId);
        break;
      case 'download-retry':
        onDownloadRetry?.(action.bookId);
        break;
      case 'download-dismiss':
        onDownloadDismiss?.(action.bookId, action.linkedRequestId);
        break;
      case 'request-approve':
        if (showRequestDetailsToggle && hasAttachedReleaseData(action.record)) {
          if (!isRequestDetailsOpen) {
            if (onRequestDetailsOpen) {
              onRequestDetailsOpen();
            } else if (onRequestDetailsToggle) {
              onRequestDetailsToggle();
            }
          }
          break;
        }
        void onRequestApprove?.(action.requestId, action.record);
        break;
      case 'request-reject':
        void onRequestReject?.(action.requestId);
        break;
      case 'request-reopen':
        void onRequestReopen?.(action.requestId);
        break;
      case 'request-cancel':
        onRequestCancel?.(action.requestId);
        break;
      case 'request-dismiss':
        onRequestDismiss?.(action.requestId);
        break;
      default:
        break;
    }
  };

  const hasActionHandler = (action: ActivityCardAction): boolean => {
    switch (action.kind) {
      case 'download-remove':
      case 'download-stop':
        return Boolean(onDownloadCancel);
      case 'download-retry':
        return Boolean(onDownloadRetry);
      case 'download-dismiss':
        return Boolean(onDownloadDismiss);
      case 'request-approve':
        return Boolean(onRequestApprove);
      case 'request-reject':
        return Boolean(onRequestReject);
      case 'request-reopen':
        return Boolean(onRequestReopen);
      case 'request-cancel':
        return Boolean(onRequestCancel);
      case 'request-dismiss':
        return Boolean(onRequestDismiss);
      default:
        return false;
    }
  };

  const actions = model.actions.filter(hasActionHandler);

  const bookData = asRecord(reviewRecord?.book_data);
  const releaseData = asRecord(reviewRecord?.release_data);
  const bookTitle = toOptionalText(bookData.title) || 'Unknown title';
  const fileTitle = toOptionalText(releaseData.title) || bookTitle;
  const fileFormat =
    toOptionalText(releaseData.format) ||
    toOptionalText(releaseData.filetype) ||
    toOptionalText(releaseData.extension) ||
    'Unknown';
  const fileSize = toOptionalText(releaseData.size) || 'Unknown';
  const sourceLabel = toSourceLabel(
    releaseData.source_display_name || releaseData.source || reviewRecord?.source_hint,
  );

  const hasAttachedRelease =
    reviewRecord?.request_level === 'release' && Object.keys(releaseData).length > 0;
  const requiresBrowseBeforeApprove = reviewRecord?.request_level === 'book' || !hasAttachedRelease;
  const showSourceField = reviewRecord?.request_level === 'release';
  const isRetryAfterFailure = Boolean(toOptionalText(reviewRecord?.last_failure_reason));

  let approveLabel = 'Approve Attached File';
  if (requiresBrowseBeforeApprove) {
    approveLabel = isRetryAfterFailure ? 'Browse Releases To Retry' : 'Browse Releases To Approve';
  }
  const canMarkAsApprovedWithoutRelease = requiresBrowseBeforeApprove && !hasAttachedRelease;

  const provider = toOptionalText(bookData.provider)?.toLowerCase();
  const providerId = toOptionalText(bookData.provider_id);
  const canBrowseAlternatives = Boolean(provider && providerId);

  const rejectConfirmHandler = onRequestRejectConfirm || onRequestReject;
  const requestedAt = reviewRecord ? formatDateTime(reviewRecord.created_at) : '';
  const requestType = reviewRecord?.content_type === 'audiobook' ? 'Audiobook' : 'Book';
  const titleAuthorLine = item.author ? `${item.title} — ${item.author}` : item.title;
  const titleLineClassName = isDetailsExpanded
    ? 'text-sm leading-tight min-w-0 whitespace-normal wrap-break-word'
    : 'text-sm truncate leading-tight min-w-0';
  let missingAttachedReleaseMessage =
    'No attached release data is available. Choose a release before approval.';
  if (reviewRecord?.request_level === 'book') {
    missingAttachedReleaseMessage = isRetryAfterFailure
      ? 'Previous download failed. Choose a release before re-approving.'
      : 'This is a book-level request without an attached file. Choose a release before approval.';
  } else if (isRetryAfterFailure) {
    missingAttachedReleaseMessage =
      'Previous download failed and the attached release was cleared. Choose a release before re-approving.';
  }

  const canShowDownloadLink =
    item.kind === 'download' &&
    item.visualStatus === 'complete' &&
    Boolean(item.downloadBookId) &&
    Boolean(item.downloadPath);

  const titleNode =
    canShowDownloadLink && item.downloadBookId ? (
      <Tooltip content="Download file" position="top" delay={0}>
        <a
          href={withBasePath(`/api/localdownload?id=${encodeURIComponent(item.downloadBookId)}`)}
          className="text-sky-600 hover:underline"
        >
          {item.title}
        </a>
      </Tooltip>
    ) : (
      item.title
    );

  return (
    <div className={`-mx-4 cursor-default px-4 py-2 ${isSelected ? 'relative' : 'hover-row'}`}>
      {isSelected && (
        <span
          aria-hidden="true"
          className="absolute top-2 bottom-2 left-0 w-1 bg-gray-400/80 dark:bg-gray-500/80"
        />
      )}
      <div className="flex items-start gap-3">
        {/* Artwork */}
        <div className="h-18 w-12 shrink-0 overflow-hidden rounded-sm bg-gray-200 dark:bg-gray-700">
          {item.preview ? (
            <img
              src={item.preview}
              alt={`${item.title} cover`}
              className="h-full w-full object-cover object-top"
            />
          ) : (
            <BookFallback />
          )}
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1 py-0.5">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <Tooltip
                content={!isDetailsExpanded && titleOverflow ? titleAuthorLine : undefined}
                delay={0}
                position="bottom"
                triggerClassName="block max-w-full"
                alwaysWrap
              >
                <p ref={titleLineRef} className={titleLineClassName}>
                  <span className="font-semibold">{titleNode}</span>
                  {item.author && <span className="text-xs opacity-60"> — {item.author}</span>}
                </p>
              </Tooltip>
            </div>
            <div className="-my-1 inline-flex shrink-0 items-center gap-1">
              {actions.map((action) => {
                const config = actionUiConfig(action);
                const icon =
                  action.kind === 'request-approve' && isRetryAfterFailure ? 'retry' : config.icon;
                const actionTitle =
                  action.kind === 'request-approve' && isRetryAfterFailure ? 'Retry' : config.title;
                return (
                  <Tooltip
                    key={actionKey(action)}
                    content={actionTitle}
                    delay={0}
                    position="bottom"
                  >
                    <IconButton
                      title={actionTitle}
                      className={config.className}
                      onClick={() => runAction(action)}
                    >
                      <ActionIcon icon={icon} />
                    </IconButton>
                  </Tooltip>
                );
              })}
              {showRequestDetailsToggle && onRequestDetailsToggle && (
                <IconButton
                  title={isDetailsExpanded ? 'Hide details' : 'Show details'}
                  className="hover-action text-gray-500"
                  onClick={onRequestDetailsToggle}
                >
                  <svg
                    className={`h-4 w-4 transition-transform ${isDetailsExpanded ? 'rotate-180' : ''}`}
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    aria-hidden="true"
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="m6 9 6 6 6-6" />
                  </svg>
                </IconButton>
              )}
            </div>
          </div>

          <p className="mt-0.5 truncate text-[11px] leading-tight opacity-60" title={item.metaLine}>
            {item.metaLine}
          </p>

          {noteLine && (
            <p className="mt-0.5 truncate text-[11px] italic opacity-60" title={noteLine}>
              {noteLine}
            </p>
          )}

          <div className="mt-1.5 flex min-w-0 items-center gap-2">
            {model.badges.map((badge, index) => {
              const badgeId = `${badge.key}-${index}`;
              const badgeStyle = STATUS_BADGE_STYLES[badge.visualStatus];
              const progressConfig = badge.isActiveDownload
                ? getProgressConfig(badge.visualStatus, badge.progress)
                : null;

              const isError = badge.visualStatus === 'error';

              return (
                <Tooltip
                  key={badgeId}
                  content={badgeOverflow[badgeId] ? badge.text : undefined}
                  delay={0}
                  position="bottom"
                  unstyled
                  interactive={isError}
                  className={STATUS_TOOLTIP_CLASSES[badge.visualStatus]}
                >
                  <span
                    ref={(element) => {
                      if (element) {
                        badgeRefs.current[badgeId] = element;
                      } else {
                        delete badgeRefs.current[badgeId];
                      }
                    }}
                    className={`relative truncate rounded-md px-2 py-0.5 text-[11px] font-medium ${badgeStyle.bg} ${badgeStyle.text} ${badge.isActiveDownload ? 'min-w-0 flex-1' : 'inline-block max-w-full'}`}
                  >
                    {progressConfig && badgeStyle.fillColor && (
                      <span
                        className="absolute inset-y-0 left-0 overflow-hidden rounded-md transition-[width] duration-300"
                        style={{ width: `${progressConfig.percent}%` }}
                      >
                        <span
                          className="absolute inset-0 rounded-md"
                          style={{ backgroundColor: badgeStyle.fillColor }}
                        />
                        <span
                          className="activity-wave absolute inset-0 rounded-md opacity-30"
                          style={{
                            background:
                              'linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.55) 50%, transparent 100%)',
                            backgroundSize: '200% 100%',
                          }}
                        />
                      </span>
                    )}
                    <span className="relative">{badge.text}</span>
                  </span>
                </Tooltip>
              );
            })}
          </div>

          {isRequestDetailsOpen && reviewRecord && reviewApproveHandler ? (
            <ReviewInlinePanel
              key={`review-${reviewRecord.id}-${reviewRecord.updated_at ?? 'unknown'}`}
              reviewRecord={reviewRecord}
              showSourceField={showSourceField}
              requestedAt={requestedAt}
              requestType={requestType}
              sourceLabel={sourceLabel}
              hasAttachedRelease={hasAttachedRelease}
              missingAttachedReleaseMessage={missingAttachedReleaseMessage}
              approveLabel={approveLabel}
              canMarkAsApprovedWithoutRelease={canMarkAsApprovedWithoutRelease}
              canBrowseAlternatives={canBrowseAlternatives}
              fileTitle={fileTitle}
              fileSize={fileSize}
              fileFormat={fileFormat}
              requiresBrowseBeforeApprove={requiresBrowseBeforeApprove}
              reviewApproveHandler={reviewApproveHandler}
            />
          ) : null}

          {isRequestRejectOpen && typeof item.requestId === 'number' && rejectConfirmHandler ? (
            <RejectInlinePanel
              key={`reject-${item.requestId}-${reviewRecord?.id ?? 'none'}-${reviewRecord?.updated_at ?? 'unknown'}`}
              requestId={item.requestId}
              itemTitle={item.title}
              onRequestRejectClose={onRequestRejectClose}
              onRequestRejectConfirm={rejectConfirmHandler}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
};

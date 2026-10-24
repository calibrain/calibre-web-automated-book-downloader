import { t } from '../../i18n';
import type { Book, RequestRecord, StatusData } from '../../types';
import { getDownloadsCount } from '../../types';
import { STATUS_LABELS, isActiveDownloadStatus } from './activityStyles.js';
import type { ActivityItem, ActivityVisualStatus } from './activityTypes';

export type DownloadStatusKey = Extract<
  keyof StatusData,
  'queued' | 'resolving' | 'locating' | 'downloading' | 'complete' | 'error' | 'cancelled'
>;

const toText = (value: unknown, fallback: string): string => {
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  return fallback;
};

const toOptionalText = (value: unknown): string | undefined => {
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  return undefined;
};

const toNumber = (value: unknown, fallback = 0): number => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  return fallback;
};

const toEpochMillis = (value: unknown): number => {
  const parsed = toNumber(value, 0);
  if (parsed <= 0) {
    return 0;
  }
  // Queue `added_time` values are epoch seconds; request/history timestamps are ms.
  // Normalize to ms so sorting is consistent across merged activity items.
  if (parsed >= 1_000_000_000 && parsed < 1_000_000_000_000) {
    return parsed * 1000;
  }
  return parsed;
};

const toSourceLabel = (value: unknown): string | undefined => {
  const text = toOptionalText(value);
  if (!text) {
    return undefined;
  }
  return text
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
};

const joinMetaParts = (parts: Array<string | undefined>): string => {
  return parts.filter((part): part is string => Boolean(part && part.trim())).join(' · ');
};

const statusKeyToVisualStatus = (statusKey: DownloadStatusKey): ActivityVisualStatus => {
  if (statusKey === 'queued') return 'queued';
  if (statusKey === 'resolving') return 'resolving';
  if (statusKey === 'locating') return 'locating';
  if (statusKey === 'downloading') return 'downloading';
  if (statusKey === 'complete') return 'complete';
  if (statusKey === 'error') return 'error';
  return 'cancelled';
};

const getDownloadProgress = (
  status: ActivityVisualStatus,
  bookProgress: unknown,
): number | undefined => {
  if (status === 'queued') return 5;
  if (status === 'resolving') return 15;
  if (status === 'locating') return 90;
  if (status === 'downloading') {
    const progress =
      typeof bookProgress === 'number' ? Math.max(0, Math.min(100, bookProgress)) : 0;
    return Math.max(0, Math.min(100, 20 + progress * 0.8));
  }
  return undefined;
};

export const downloadToActivityItem = (book: Book, statusKey: DownloadStatusKey): ActivityItem => {
  const visualStatus = statusKeyToVisualStatus(statusKey);
  const requestId =
    typeof book.request_id === 'number' && Number.isFinite(book.request_id) && book.request_id > 0
      ? Math.trunc(book.request_id)
      : undefined;
  const downloadsCount = getDownloadsCount(book);
  const downloadsText =
    downloadsCount != null
      ? t('{count} downloads', { count: downloadsCount.toLocaleString() })
      : undefined;
  const metaLine = joinMetaParts([
    toOptionalText(book.format)?.toUpperCase(),
    toOptionalText(book.size),
    toOptionalText(book.source_display_name) || toSourceLabel(book.source),
    downloadsText,
    toOptionalText(book.username),
  ]);
  const progress = getDownloadProgress(visualStatus, book.progress);
  const statusDetail = toOptionalText(book.status_message);
  const downloadRetryAvailable = book.retry_available === true;

  return {
    id: book.id,
    kind: 'download',
    visualStatus,
    title: toText(book.title, t('Unknown title')),
    author: toText(book.author, t('Unknown author')),
    preview: toOptionalText(book.preview),
    metaLine,
    statusLabel: t(STATUS_LABELS[visualStatus]),
    statusDetail,
    progress,
    progressAnimated: isActiveDownloadStatus(visualStatus),
    timestamp: toEpochMillis(book.added_time),
    username: toOptionalText(book.username),
    downloadBookId: book.id,
    downloadRetryAvailable,
    downloadPath: toOptionalText(book.download_path),
    sizeRaw: toOptionalText(book.size),
    downloads: downloadsCount ?? undefined,
    requestId,
  };
};

const parseRecordData = (
  value: Record<string, unknown> | null | undefined,
): Record<string, unknown> => value ?? {};

const requestStatusToVisualStatus = (status: RequestRecord['status']): ActivityVisualStatus => {
  if (status === 'pending') return 'pending';
  if (status === 'fulfilled') return 'fulfilled';
  if (status === 'rejected') return 'rejected';
  return 'cancelled';
};

const buildRequestMetaLine = (
  record: RequestRecord,
  bookData: Record<string, unknown>,
  releaseData: Record<string, unknown>,
  viewerRole: 'user' | 'admin',
): string => {
  if (record.request_level === 'book') {
    const contentType = toOptionalText(record.content_type || bookData.content_type)?.toLowerCase();
    const requestTypeLabel =
      contentType === 'audiobook' ? t('Audiobook request') : t('Book request');
    const username = viewerRole === 'admin' ? toOptionalText(record.username) : undefined;
    return joinMetaParts([requestTypeLabel, username]);
  }

  const format = toOptionalText(releaseData.format)?.toUpperCase();
  const size = toOptionalText(releaseData.size);
  const source = toSourceLabel(releaseData.source || record.source_hint);
  const username = viewerRole === 'admin' ? toOptionalText(record.username) : undefined;

  const line = joinMetaParts([format, size, source, username]);
  return line || joinMetaParts([t('Release request'), username]);
};

export const requestToActivityItem = (
  record: RequestRecord,
  viewerRole: 'user' | 'admin',
): ActivityItem => {
  const visualStatus = requestStatusToVisualStatus(record.status);
  const bookData = parseRecordData(record.book_data);
  const releaseData = parseRecordData(record.release_data);

  const timestamp = Number.isFinite(Date.parse(record.created_at))
    ? Date.parse(record.created_at)
    : 0;

  return {
    id: `request-${record.id}`,
    kind: 'request',
    visualStatus,
    title: toText(bookData.title ?? releaseData.title, t('Unknown title')),
    author: toText(bookData.author ?? releaseData.author, t('Unknown author')),
    preview: toOptionalText(bookData.preview) || toOptionalText(releaseData.preview),
    metaLine: buildRequestMetaLine(record, bookData, releaseData, viewerRole),
    statusLabel: t(STATUS_LABELS[visualStatus]),
    adminNote: toOptionalText(record.admin_note),
    timestamp,
    username: toOptionalText(record.username),
    requestId: record.id,
    requestLevel: record.request_level,
    requestNote: toOptionalText(record.note),
    requestRecord: record,
  };
};

/** The id of the download a fulfilled request item was delivered through, if listed. */
export const linkedDownloadIdForRequest = (
  item: ActivityItem,
  latestDownloadIdByRequestId: Map<number, string>,
): string | null => {
  if (item.kind !== 'request' || item.visualStatus !== 'fulfilled') {
    return null;
  }

  const sourceId = item.requestRecord?.release_data?.source_id;
  if (typeof sourceId === 'string' && sourceId.trim()) {
    return sourceId.trim();
  }

  // Non-admins don't receive the release's source_id (#1418), so their request is
  // matched to the download queued for it instead.
  if (typeof item.requestId !== 'number') {
    return null;
  }
  return latestDownloadIdByRequestId.get(item.requestId) ?? null;
};

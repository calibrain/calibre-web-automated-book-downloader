import { useEffect, useEffectEvent, useRef } from 'react';

import { t } from '../../i18n';
import type { AppConfig, StatusData } from '../../types';
import { withBasePath } from '../../utils/basePath';

interface UseStatusChangeNotificationsOptions {
  currentStatus: StatusData;
  config: AppConfig | null;
  showToast: (message: string, type: 'info' | 'success' | 'error') => void;
  openDownloadsSidebar: () => void;
  bookToReleaseMap: Record<string, string[]>;
  markBookCompleted: (bookId: string) => void;
}

export const useStatusChangeNotifications = ({
  currentStatus,
  config,
  showToast,
  openDownloadsSidebar,
  bookToReleaseMap,
  markBookCompleted,
}: UseStatusChangeNotificationsOptions): void => {
  const prevStatusRef = useRef<StatusData>({});
  const handleStatusTransition = useEffectEvent(
    (prevStatus: StatusData, nextStatus: StatusData) => {
      const autoDownloadContentTypes = Array.isArray(config?.download_to_browser_content_types)
        ? config.download_to_browser_content_types
        : [];
      const canAutoDownloadContentType = (downloadContentType?: string): boolean => {
        const contentTypeKey =
          (downloadContentType || '').trim().toLowerCase() === 'audiobook' ? 'audiobook' : 'book';
        return autoDownloadContentTypes.includes(contentTypeKey);
      };

      const prevQueued = prevStatus.queued || {};
      const currQueued = nextStatus.queued || {};
      let shouldOpenDownloadsSidebar = false;
      Object.keys(currQueued).forEach((bookId) => {
        if (!prevQueued[bookId]) {
          const book = currQueued[bookId];
          showToast(t('{title} added to queue', { title: book.title || t('Book') }), 'info');
          if (config?.auto_open_downloads_sidebar !== false) {
            shouldOpenDownloadsSidebar = true;
          }
        }
      });
      if (shouldOpenDownloadsSidebar) {
        openDownloadsSidebar();
      }

      const prevDownloading = prevStatus.downloading || {};
      const currDownloading = nextStatus.downloading || {};
      Object.keys(currDownloading).forEach((bookId) => {
        if (!prevDownloading[bookId]) {
          const book = currDownloading[bookId];
          showToast(t('{title} started downloading', { title: book.title || t('Book') }), 'info');
        }
      });

      const prevComplete = prevStatus.complete || {};
      const currComplete = nextStatus.complete || {};
      Object.keys(currComplete).forEach((bookId) => {
        if (!prevComplete[bookId]) {
          const book = currComplete[bookId];
          showToast(t('{title} completed', { title: book.title || t('Book') }), 'success');

          if (book.download_path && canAutoDownloadContentType(book.content_type)) {
            const link = document.createElement('a');
            link.href = withBasePath(`/api/localdownload?id=${encodeURIComponent(bookId)}`);
            link.download = '';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
          }

          Object.entries(bookToReleaseMap).forEach(([metadataBookId, releaseIds]) => {
            if (releaseIds.includes(bookId)) {
              markBookCompleted(metadataBookId);
            }
          });
        }
      });

      const prevError = prevStatus.error || {};
      const currError = nextStatus.error || {};
      Object.keys(currError).forEach((bookId) => {
        if (!prevError[bookId]) {
          const book = currError[bookId];
          const errorMsg = t(book.status_message || 'Download failed');
          showToast(`${book.title || t('Book')}: ${errorMsg}`, 'error');
        }
      });
    },
  );

  useEffect(() => {
    const prevStatus = prevStatusRef.current;
    if (!prevStatus || Object.keys(prevStatus).length === 0) {
      prevStatusRef.current = currentStatus;
      return;
    }

    handleStatusTransition(prevStatus, currentStatus);
    prevStatusRef.current = currentStatus;
  }, [currentStatus]);
};

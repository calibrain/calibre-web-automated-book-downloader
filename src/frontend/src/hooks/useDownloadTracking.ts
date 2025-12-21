import { useState, useCallback } from 'react';
import { StatusData, ButtonStateInfo } from '../types';

interface UseDownloadTrackingReturn {
  bookToReleaseMap: Record<string, string[]>;
  sessionCompletedBookIds: Set<string>;
  trackRelease: (bookId: string, releaseId: string) => void;
  markBookCompleted: (bookId: string) => void;
  clearTracking: () => void;
  getButtonState: (bookId: string) => ButtonStateInfo;
  getUniversalButtonState: (bookId: string) => ButtonStateInfo;
}

export function useDownloadTracking(currentStatus: StatusData): UseDownloadTrackingReturn {
  // Track mapping of metadata book IDs to release source IDs for universal mode
  const [bookToReleaseMap, setBookToReleaseMap] = useState<Record<string, string[]>>({});
  // Session-only tracking of completed book IDs (survives clearCompleted, resets on refresh)
  const [sessionCompletedBookIds, setSessionCompletedBookIds] = useState<Set<string>>(new Set());

  const trackRelease = useCallback((bookId: string, releaseId: string) => {
    setBookToReleaseMap(prev => ({
      ...prev,
      [bookId]: [...(prev[bookId] || []), releaseId],
    }));
  }, []);

  const markBookCompleted = useCallback((bookId: string) => {
    setSessionCompletedBookIds(prev => new Set([...prev, bookId]));
  }, []);

  const clearTracking = useCallback(() => {
    setBookToReleaseMap({});
    setSessionCompletedBookIds(new Set());
  }, []);

  // Get button state for a book in direct mode
  const getButtonState = useCallback((bookId: string): ButtonStateInfo => {
    if (currentStatus.error && currentStatus.error[bookId]) {
      return { text: 'Failed', state: 'error' };
    }
    if (currentStatus.complete && currentStatus.complete[bookId]) {
      return { text: 'Downloaded', state: 'complete' };
    }
    if (currentStatus.downloading && currentStatus.downloading[bookId]) {
      const book = currentStatus.downloading[bookId];
      return {
        text: 'Downloading',
        state: 'downloading',
        progress: book.progress,
      };
    }
    if (currentStatus.resolving && currentStatus.resolving[bookId]) {
      return { text: 'Resolving', state: 'resolving' };
    }
    if (currentStatus.queued && currentStatus.queued[bookId]) {
      return { text: 'Queued', state: 'queued' };
    }
    return { text: 'Download', state: 'download' };
  }, [currentStatus]);

  // Get button state for a metadata book in universal mode
  const getUniversalButtonState = useCallback((bookId: string): ButtonStateInfo => {
    const releaseIds = bookToReleaseMap[bookId] || [];

    // No releases downloaded yet - check session state
    if (releaseIds.length === 0) {
      if (sessionCompletedBookIds.has(bookId)) {
        return { text: 'Downloaded', state: 'complete' };
      }
      return { text: 'Get', state: 'download' };
    }

    // Check each release ID and find the most relevant state
    let bestState: ButtonStateInfo = { text: 'Get', state: 'download' };
    let foundActiveState = false;

    for (const releaseId of releaseIds) {
      if (currentStatus.downloading && currentStatus.downloading[releaseId]) {
        const downloadingBook = currentStatus.downloading[releaseId];
        return {
          text: 'Downloading',
          state: 'downloading',
          progress: downloadingBook.progress,
        };
      }

      if (currentStatus.resolving && currentStatus.resolving[releaseId]) {
        return { text: 'Resolving', state: 'resolving' };
      }

      if (currentStatus.queued && currentStatus.queued[releaseId]) {
        return { text: 'Queued', state: 'queued' };
      }

      if (!foundActiveState) {
        if (currentStatus.complete && currentStatus.complete[releaseId]) {
          bestState = { text: 'Downloaded', state: 'complete' };
          foundActiveState = true;
        } else if (currentStatus.error && currentStatus.error[releaseId]) {
          if (bestState.state === 'download') {
            bestState = { text: 'Failed', state: 'error' };
          }
        }
      }
    }

    // Check session tracking if no state found
    if (bestState.state === 'download' && sessionCompletedBookIds.has(bookId)) {
      return { text: 'Downloaded', state: 'complete' };
    }

    return bestState;
  }, [currentStatus, bookToReleaseMap, sessionCompletedBookIds]);

  return {
    bookToReleaseMap,
    sessionCompletedBookIds,
    trackRelease,
    markBookCompleted,
    clearTracking,
    getButtonState,
    getUniversalButtonState,
  };
}

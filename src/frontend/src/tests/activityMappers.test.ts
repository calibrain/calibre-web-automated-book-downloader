import { describe, it, expect } from 'vitest';

import {
  downloadToActivityItem,
  linkedDownloadIdForRequest,
  requestToActivityItem,
} from '../components/activity/activityMappers';
import type { Book, RequestRecord } from '../types/index';

const makeBook = (overrides: Partial<Book> = {}): Book => ({
  id: 'book-1',
  title: 'The Test Book',
  author: 'Test Author',
  ...overrides,
});

const makeRequest = (overrides: Partial<RequestRecord> = {}): RequestRecord => ({
  id: 42,
  user_id: 7,
  status: 'pending',
  source_hint: 'prowlarr',
  content_type: 'ebook',
  request_level: 'release',
  policy_mode: 'request_release',
  book_data: {
    title: 'Request Title',
    author: 'Request Author',
    preview: 'https://example.com/cover.jpg',
  },
  release_data: {
    source: 'prowlarr',
    format: 'epub',
    size: '2 MB',
  },
  note: 'please add this',
  admin_note: null,
  reviewed_by: null,
  reviewed_at: null,
  created_at: '2026-02-13T12:00:00Z',
  updated_at: '2026-02-13T12:00:00Z',
  username: 'alice',
  ...overrides,
});

describe('activityMappers.downloadToActivityItem', () => {
  it('maps every download status key to its visual status', () => {
    const statusExpectations: Array<{
      statusKey:
        | 'queued'
        | 'resolving'
        | 'locating'
        | 'downloading'
        | 'complete'
        | 'error'
        | 'cancelled';
      expectedVisualStatus: string;
    }> = [
      { statusKey: 'queued', expectedVisualStatus: 'queued' },
      { statusKey: 'resolving', expectedVisualStatus: 'resolving' },
      { statusKey: 'locating', expectedVisualStatus: 'locating' },
      { statusKey: 'downloading', expectedVisualStatus: 'downloading' },
      { statusKey: 'complete', expectedVisualStatus: 'complete' },
      { statusKey: 'error', expectedVisualStatus: 'error' },
      { statusKey: 'cancelled', expectedVisualStatus: 'cancelled' },
    ];

    statusExpectations.forEach(({ statusKey, expectedVisualStatus }) => {
      const item = downloadToActivityItem(makeBook(), statusKey);
      expect(item.visualStatus).toBe(expectedVisualStatus);
    });
  });

  it('maps download items with meta line and status fields', () => {
    const item = downloadToActivityItem(
      makeBook({
        format: 'epub',
        size: '3 MB',
        source_display_name: 'Direct Download',
        username: 'alice',
        added_time: 123,
      }),
      'queued',
    );

    expect(item.kind).toBe('download');
    expect(item.visualStatus).toBe('queued');
    expect(item.statusLabel).toBe('Queued');
    expect(item.metaLine).toBe('EPUB · 3 MB · Direct Download · alice');
    expect(item.progress).toBe(5);
    expect(item.progressAnimated).toBe(true);
    expect(item.timestamp).toBe(123);
  });

  it('normalizes epoch-second added_time values to milliseconds', () => {
    const item = downloadToActivityItem(
      makeBook({
        added_time: 1700000000,
      }),
      'complete',
    );

    expect(item.timestamp).toBe(1700000000000);
  });

  it('maps downloading progress using 20 + progress*0.8', () => {
    const item = downloadToActivityItem(makeBook({ progress: 60 }), 'downloading');
    expect(item.visualStatus).toBe('downloading');
    expect(item.progress).toBe(68);
  });

  it('falls back to normalized source name when source_display_name is missing', () => {
    const item = downloadToActivityItem(makeBook({ source: 'direct_download' }), 'complete');
    expect(item.metaLine).toBe('Direct Download');
  });

  it('omits empty meta parts cleanly', () => {
    const item = downloadToActivityItem(makeBook({ format: 'epub', size: undefined }), 'error');
    expect(item.metaLine).toBe('EPUB');
  });
});

describe('activityMappers.requestToActivityItem', () => {
  it('maps request statuses to visual statuses', () => {
    const statuses: Array<{ input: RequestRecord['status']; expected: string }> = [
      { input: 'pending', expected: 'pending' },
      { input: 'fulfilled', expected: 'fulfilled' },
      { input: 'rejected', expected: 'rejected' },
      { input: 'cancelled', expected: 'cancelled' },
    ];

    statuses.forEach(({ input, expected }) => {
      const item = requestToActivityItem(makeRequest({ status: input }), 'user');
      expect(item.visualStatus).toBe(expected);
    });
  });

  it('maps release-level admin request with release meta and username', () => {
    const item = requestToActivityItem(makeRequest(), 'admin');

    expect(item.kind).toBe('request');
    expect(item.visualStatus).toBe('pending');
    expect(item.metaLine).toBe('EPUB · 2 MB · Prowlarr · alice');
    expect(item.requestId).toBe(42);
    expect(item.requestLevel).toBe('release');
    expect(item.requestNote).toBe('please add this');
    expect(item.statusLabel).toBe('Pending');
    expect(item.timestamp > 0).toBeTruthy();
  });

  it('maps book-level user request without username in meta line', () => {
    const item = requestToActivityItem(
      makeRequest({
        request_level: 'book',
        release_data: null,
        source_hint: '*',
      }),
      'user',
    );

    expect(item.metaLine).toBe('Book request');
  });

  it('maps audiobook book-level request with audiobook label', () => {
    const item = requestToActivityItem(
      makeRequest({
        request_level: 'book',
        content_type: 'audiobook',
        release_data: null,
        source_hint: '*',
      }),
      'admin',
    );

    expect(item.metaLine).toBe('Audiobook request · alice');
  });

  it('maps rejected requests with admin note', () => {
    const item = requestToActivityItem(
      makeRequest({
        status: 'rejected',
        admin_note: 'Not available',
      }),
      'user',
    );

    expect(item.visualStatus).toBe('rejected');
    expect(item.adminNote).toBe('Not available');
  });

  it('does not append username to meta line for user viewer role', () => {
    const item = requestToActivityItem(makeRequest(), 'user');
    expect(item.metaLine).toBe('EPUB · 2 MB · Prowlarr');
  });
});

const fulfilled = (releaseData: Record<string, unknown> | null) =>
  requestToActivityItem(
    makeRequest({ status: 'fulfilled', request_level: 'book', release_data: releaseData }),
    'user',
  );

describe('activityMappers.linkedDownloadIdForRequest', () => {
  it("links through the release's source_id when the viewer has it", () => {
    const item = fulfilled({ source_id: '21:https://tracker.example/t/1', format: 'epub' });

    expect(linkedDownloadIdForRequest(item, new Map([[42, 'dl_other']]))).toBe(
      '21:https://tracker.example/t/1',
    );
  });

  it('falls back to the download queued for the request when source_id is withheld', () => {
    const item = fulfilled({ format: 'epub', size: '2 MB' });

    expect(linkedDownloadIdForRequest(item, new Map([[42, 'dl_abc']]))).toBe('dl_abc');
    expect(linkedDownloadIdForRequest(item, new Map())).toBeNull();
  });

  it('does not link a request that has not been fulfilled', () => {
    const item = requestToActivityItem(makeRequest({ status: 'pending' }), 'user');

    expect(linkedDownloadIdForRequest(item, new Map([[42, 'dl_abc']]))).toBeNull();
  });
});

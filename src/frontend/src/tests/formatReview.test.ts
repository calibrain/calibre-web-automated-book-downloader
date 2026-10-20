import { describe, it, expect } from 'vitest';

import {
  fileFormat,
  formatBytes,
  groupFilesByFormat,
  ignoredForFormats,
  type ReviewFile,
} from '../utils/formatReview';

const files: ReviewFile[] = [
  { path: 'Book/Book.epub', size: 1000 },
  { path: 'Book/Book.mobi', size: 2000 },
  { path: 'Book/Book.pdf', size: null },
  { path: 'Book/Book2.epub', size: 500 },
  { path: 'Book/cover.jpg', size: 10 },
];
const bookFiles = new Set(files.filter((f) => !f.path.endsWith('.jpg')).map((f) => f.path));

describe('formatReview.fileFormat', () => {
  it('lowercases the extension and handles missing ones', () => {
    expect(fileFormat('a/B.EPUB')).toBe('epub');
    expect(fileFormat('a\\b\\c.m4b')).toBe('m4b');
    expect(fileFormat('README')).toBe('');
    expect(fileFormat('.hidden')).toBe('');
  });
});

describe('formatReview.groupFilesByFormat', () => {
  it('groups only book files, biggest group first, summing known sizes', () => {
    const groups = groupFilesByFormat(files, bookFiles);
    expect(groups.map((g) => g.format)).toEqual(['epub', 'mobi', 'pdf']);
    expect(groups[0].totalBytes).toBe(1500);
    expect(groups[2].totalBytes).toBeNull();
  });
});

describe('formatReview.ignoredForFormats', () => {
  it('returns every file of unselected formats', () => {
    const groups = groupFilesByFormat(files, bookFiles);
    expect(ignoredForFormats(groups, new Set(['epub']))).toEqual([
      'Book/Book.mobi',
      'Book/Book.pdf',
    ]);
    expect(ignoredForFormats(groups, new Set(['epub', 'mobi', 'pdf']))).toEqual([]);
  });
});

describe('formatReview.formatBytes', () => {
  it('formats sizes and tolerates unknown', () => {
    expect(formatBytes(null)).toBe('');
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB');
  });
});

import { describe, it, expect } from 'vitest';

import type { PackBook } from '../types';
import {
  applyIgnoredFiles,
  describePackPlan,
  parseSeriesPositionInput,
  setBookIgnored,
  toBookPlanPayload,
  updateReviewBook,
} from '../utils/packReview';

const books: PackBook[] = [
  { title: 'Leviathan Wakes', series_position: 1, year: 2011, files: ['a.m4b'] },
  { title: 'Caliban’s War', series_position: 2, year: 2012, files: ['b.m4b', 'b2.m4b'] },
];

describe('packReview.updateReviewBook', () => {
  it('replaces one book without touching the others', () => {
    const next = updateReviewBook(books, 1, { title: 'Caliban’s War (Unabridged)' });
    expect(next[0]).toBe(books[0]);
    expect(next[1]).toEqual({ ...books[1], title: 'Caliban’s War (Unabridged)' });
    expect(books[1].title).toBe('Caliban’s War');
  });
});

describe('packReview.parseSeriesPositionInput', () => {
  it('accepts whole and fractional positions', () => {
    expect(parseSeriesPositionInput('3')).toBe(3);
    expect(parseSeriesPositionInput('2.5')).toBe(2.5);
  });

  it('treats blank or junk as no position', () => {
    expect(parseSeriesPositionInput('')).toBeNull();
    expect(parseSeriesPositionInput('abc')).toBeNull();
  });
});

describe('packReview.toBookPlanPayload', () => {
  it('trims titles, drops books without a title, and keeps file lists', () => {
    const edited = updateReviewBook(books, 0, { title: '   ' });
    expect(toBookPlanPayload(edited)).toEqual([
      { title: 'Caliban’s War', series_position: 2, year: 2012, files: ['b.m4b', 'b2.m4b'] },
    ]);
  });
});

describe('packReview.describePackPlan', () => {
  it('summarises books, files and ignored sidecars', () => {
    expect(describePackPlan(books, ['a.txt', 'cover.jpg'])).toBe(
      '2 books · 3 files · 2 files ignored',
    );
    expect(describePackPlan([books[0]], [])).toBe('1 book · 1 file');
  });
});

describe('packReview.applyIgnoredFiles', () => {
  it('removes ignored files and drops books left empty', () => {
    const result = applyIgnoredFiles(books, new Set(['b2.m4b', 'a.m4b']));
    expect(result).toEqual([{ ...books[1], files: ['b.m4b'] }]);
  });

  it('returns the input untouched when nothing is ignored', () => {
    expect(applyIgnoredFiles(books, new Set())).toBe(books);
  });
});

describe('packReview.setBookIgnored', () => {
  it('adds and removes every file of a book without mutating the input', () => {
    const start = new Set<string>();
    const added = setBookIgnored(start, books[1], true);
    expect([...added].toSorted()).toEqual(['b.m4b', 'b2.m4b']);
    expect(start.size).toBe(0);
    expect(setBookIgnored(added, books[1], false).size).toBe(0);
  });
});

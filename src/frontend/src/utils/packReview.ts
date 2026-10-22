import { t } from '../i18n';
import type { PackBook } from '../types';

/** Return a copy of `books` with one entry patched; the input is not mutated. */
export function updateReviewBook(
  books: PackBook[],
  index: number,
  patch: Partial<PackBook>,
): PackBook[] {
  return books.map((book, i) => (i === index ? { ...book, ...patch } : book));
}

/** Parse a series-position text field: "3" → 3, "2.5" → 2.5, blank/junk → null. */
export function parseSeriesPositionInput(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

/** The plan sent with the download: trimmed titles, untitled books dropped. */
export function toBookPlanPayload(books: PackBook[]): PackBook[] {
  return books
    .map((book) => ({ ...book, title: book.title.trim() }))
    .filter((book) => book.title.length > 0 && book.files.length > 0);
}

/** Books with the user-ignored files removed; books left with no files are dropped. */
export function applyIgnoredFiles(books: PackBook[], ignored: ReadonlySet<string>): PackBook[] {
  if (ignored.size === 0) return books;
  return books
    .map((book) => ({ ...book, files: book.files.filter((file) => !ignored.has(file)) }))
    .filter((book) => book.files.length > 0);
}

/** Add or remove every file of a book from the ignored set (returns a new set). */
export function setBookIgnored(
  ignored: ReadonlySet<string>,
  book: PackBook,
  shouldIgnore: boolean,
): Set<string> {
  const next = new Set(ignored);
  for (const file of book.files) {
    if (shouldIgnore) next.add(file);
    else next.delete(file);
  }
  return next;
}

/** "2 books · 3 files · 2 files ignored" */
export function describePackPlan(books: PackBook[], ignored: string[]): string {
  const fileCount = books.reduce((sum, book) => sum + book.files.length, 0);
  const parts = [
    books.length === 1 ? t('1 book') : t('{count} books', { count: books.length }),
    fileCount === 1 ? t('1 file') : t('{count} files', { count: fileCount }),
  ];
  if (ignored.length > 0) {
    parts.push(
      ignored.length === 1
        ? t('1 file ignored')
        : t('{count} files ignored', { count: ignored.length }),
    );
  }
  return parts.join(' · ');
}

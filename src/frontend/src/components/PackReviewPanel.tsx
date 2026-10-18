import { useState } from 'react';

import type { PackBook, PackPlan, Release } from '../types';
import {
  applyIgnoredFiles,
  describePackPlan,
  parseSeriesPositionInput,
  setBookIgnored,
  toBookPlanPayload,
  updateReviewBook,
} from '../utils/packReview';
import { ToggleSwitch } from './shared/ToggleSwitch';

interface PackReviewPanelProps {
  release: Release;
  plan: PackPlan;
  books: PackBook[];
  onChange: (books: PackBook[]) => void;
  onBack: () => void;
  /** `null` means "treat the whole release as one book". */
  onConfirm: (books: PackBook[] | null, ignoredFiles?: string[]) => Promise<void>;
  isSubmitting: boolean;
}

const inputClassName =
  'w-full rounded-md border border-(--border-muted) bg-(--bg) px-2 py-1 text-sm text-(--text) focus:border-emerald-500 focus:outline-none';

export const PackReviewPanel = ({
  release,
  plan,
  books,
  onChange,
  onBack,
  onConfirm,
  isSubmitting,
}: PackReviewPanelProps) => {
  const [singleBook, setSingleBook] = useState(false);
  const [expandedFiles, setExpandedFiles] = useState<number | null>(null);
  const [showIgnored, setShowIgnored] = useState(false);
  const [userIgnored, setUserIgnored] = useState<ReadonlySet<string>>(new Set());

  const toggleFile = (file: string) => {
    setUserIgnored((prev) => {
      const next = new Set(prev);
      if (next.has(file)) next.delete(file);
      else next.add(file);
      return next;
    });
  };

  const payloadBooks = toBookPlanPayload(applyIgnoredFiles(books, userIgnored));
  const canConfirm = !isSubmitting && (singleBook || payloadBooks.length > 0);
  const confirmLabel = singleBook
    ? 'Download as one book'
    : `Download ${payloadBooks.length} ${payloadBooks.length === 1 ? 'book' : 'books'}`;

  return (
    <div className="flex flex-col gap-4 px-5 py-4" data-testid="pack-review-panel">
      <div>
        <h3 className="text-base font-semibold text-(--text)">
          This release contains several books
        </h3>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          <span className="font-medium text-(--text)">{release.title}</span> ·{' '}
          {describePackPlan(applyIgnoredFiles(books, userIgnored), [
            ...plan.ignored,
            ...userIgnored,
          ])}
        </p>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          Each book below is filed separately with its own title. Fix any titles before downloading
          — the author and series come from the book you searched.
        </p>
      </div>

      <div className="flex items-center justify-between rounded-lg border border-(--border-muted) px-3 py-2">
        <div>
          <p className="text-sm font-medium text-(--text)">Treat as a single book</p>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            Use this if the split is wrong and the files are really one audiobook.
          </p>
        </div>
        <ToggleSwitch
          checked={singleBook}
          onChange={setSingleBook}
          color="emerald"
          ariaLabel="Treat as a single book"
          disabled={isSubmitting}
        />
      </div>

      <div
        className={`flex flex-col divide-y divide-zinc-200/60 dark:divide-zinc-800/60 ${
          singleBook ? 'pointer-events-none opacity-40' : ''
        }`}
      >
        <div className="grid grid-cols-[minmax(0,1fr)_72px_72px_80px] gap-2 pb-1 text-xs font-medium tracking-wide text-zinc-500 uppercase dark:text-zinc-400">
          <span>Title</span>
          <span>Series #</span>
          <span>Year</span>
          <span className="text-right">Files</span>
        </div>
        {books.map((book, index) => {
          const skipped = book.files.every((file) => userIgnored.has(file));
          return (
            <div
              key={book.files[0] ?? index}
              className={`py-2 ${skipped ? 'opacity-50' : ''}`}
              data-skipped={skipped}
            >
              <div className="grid grid-cols-[minmax(0,1fr)_72px_72px_80px] items-center gap-2">
                <input
                  type="text"
                  value={book.title}
                  onChange={(e) =>
                    onChange(updateReviewBook(books, index, { title: e.target.value }))
                  }
                  aria-label={`Title for book ${index + 1}`}
                  className={inputClassName}
                  disabled={isSubmitting}
                />
                <input
                  type="text"
                  inputMode="decimal"
                  value={book.series_position ?? ''}
                  onChange={(e) =>
                    onChange(
                      updateReviewBook(books, index, {
                        series_position: parseSeriesPositionInput(e.target.value),
                      }),
                    )
                  }
                  aria-label={`Series position for book ${index + 1}`}
                  className={inputClassName}
                  disabled={isSubmitting}
                />
                <input
                  type="text"
                  inputMode="numeric"
                  value={book.year ?? ''}
                  onChange={(e) => {
                    const parsed = parseSeriesPositionInput(e.target.value);
                    onChange(
                      updateReviewBook(books, index, {
                        year: parsed === null ? null : Math.trunc(parsed),
                      }),
                    );
                  }}
                  aria-label={`Year for book ${index + 1}`}
                  className={inputClassName}
                  disabled={isSubmitting}
                />
                <button
                  type="button"
                  onClick={() => setExpandedFiles(expandedFiles === index ? null : index)}
                  className="hover-surface rounded-md px-2 py-1 text-right text-sm text-zinc-500 transition-colors dark:text-zinc-400"
                  aria-expanded={expandedFiles === index}
                >
                  {book.files.length} {book.files.length === 1 ? 'file' : 'files'}
                </button>
              </div>
              <label className="mt-1 flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
                <input
                  type="checkbox"
                  checked={skipped}
                  onChange={(e) =>
                    setUserIgnored(setBookIgnored(userIgnored, book, e.target.checked))
                  }
                  disabled={isSubmitting}
                  aria-label={`Skip book ${index + 1}`}
                />
                Skip this book
              </label>
              {expandedFiles === index && (
                <ul className="mt-2 max-h-40 overflow-y-auto rounded-md bg-(--bg-soft) px-3 py-2 font-mono text-xs break-all text-zinc-600 dark:text-zinc-300">
                  {book.files.map((file) => (
                    <li key={file}>
                      <label className="flex items-start gap-2">
                        <input
                          type="checkbox"
                          checked={!userIgnored.has(file)}
                          onChange={() => toggleFile(file)}
                          disabled={isSubmitting}
                          aria-label={`Include ${file}`}
                          className="mt-0.5"
                        />
                        <span className={userIgnored.has(file) ? 'line-through opacity-60' : ''}>
                          {file}
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>

      {plan.ignored.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setShowIgnored(!showIgnored)}
            className="text-xs text-zinc-500 underline-offset-2 hover:underline dark:text-zinc-400"
            aria-expanded={showIgnored}
          >
            {plan.ignored.length} {plan.ignored.length === 1 ? 'file' : 'files'} ignored (not a book
            format)
          </button>
          {showIgnored && (
            <ul className="mt-2 max-h-32 overflow-y-auto rounded-md bg-(--bg-soft) px-3 py-2 font-mono text-xs break-all text-zinc-600 dark:text-zinc-300">
              {plan.ignored.map((file) => (
                <li key={file}>{file}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="flex items-center justify-end gap-3 border-t border-(--border-muted) pt-4">
        <button
          type="button"
          onClick={onBack}
          disabled={isSubmitting}
          className="hover-surface rounded-lg px-3 py-1.5 text-sm font-medium text-(--text) transition-colors disabled:opacity-50"
        >
          &larr; Back
        </button>
        <button
          type="button"
          onClick={() => void onConfirm(singleBook ? null : payloadBooks, [...userIgnored])}
          disabled={!canConfirm}
          className="rounded-lg bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isSubmitting ? 'Queuing…' : confirmLabel}
        </button>
      </div>
    </div>
  );
};

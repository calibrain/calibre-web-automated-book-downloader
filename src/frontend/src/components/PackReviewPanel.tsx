import { useState } from 'react';

import { t } from '../i18n';
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
  let confirmLabel = t('Download {count} books', { count: payloadBooks.length });
  if (singleBook) {
    confirmLabel = t('Download as one book');
  } else if (payloadBooks.length === 1) {
    confirmLabel = t('Download 1 book');
  }

  return (
    <div className="flex flex-col gap-4 px-5 py-4" data-testid="pack-review-panel">
      <div>
        <h3 className="text-base font-semibold text-(--text)">
          {t('This release contains several books')}
        </h3>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          <span className="font-medium text-(--text)">{release.title}</span> ·{' '}
          {describePackPlan(applyIgnoredFiles(books, userIgnored), [
            ...plan.ignored,
            ...userIgnored,
          ])}
        </p>
        <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
          {t(
            'Each book below is filed separately with its own title. Fix any titles before downloading — the author and series come from the book you searched.',
          )}
        </p>
      </div>

      <div className="flex items-center justify-between rounded-lg border border-(--border-muted) px-3 py-2">
        <div>
          <p className="text-sm font-medium text-(--text)">{t('Treat as a single book')}</p>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            {t('Use this if the split is wrong and the files are really one audiobook.')}
          </p>
        </div>
        <ToggleSwitch
          checked={singleBook}
          onChange={setSingleBook}
          color="emerald"
          ariaLabel={t('Treat as a single book')}
          disabled={isSubmitting}
        />
      </div>

      <div
        className={`flex flex-col divide-y divide-zinc-200/60 dark:divide-zinc-800/60 ${
          singleBook ? 'pointer-events-none opacity-40' : ''
        }`}
      >
        <div className="grid grid-cols-[minmax(0,1fr)_72px_72px_80px] gap-2 pb-1 text-xs font-medium tracking-wide text-zinc-500 uppercase dark:text-zinc-400">
          <span>{t('Title')}</span>
          <span>{t('Series #')}</span>
          <span>{t('Year')}</span>
          <span className="text-right">{t('Files')}</span>
        </div>
        {books.map((book, index) => {
          const skipped = book.files.every((file) => userIgnored.has(file));
          const ignoredCount = book.files.filter((file) => userIgnored.has(file)).length;
          const partiallyIgnored = !skipped && ignoredCount > 0;
          const fileCountLabel =
            book.files.length === 1
              ? t('1 file')
              : t('{count} files', { count: book.files.length });
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
                  aria-label={t('Title for book {number}', { number: index + 1 })}
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
                  aria-label={t('Series position for book {number}', { number: index + 1 })}
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
                  aria-label={t('Year for book {number}', { number: index + 1 })}
                  className={inputClassName}
                  disabled={isSubmitting}
                />
                <button
                  type="button"
                  onClick={() => setExpandedFiles(expandedFiles === index ? null : index)}
                  className="hover-surface rounded-md px-2 py-1 text-right text-sm text-zinc-500 transition-colors dark:text-zinc-400"
                  aria-expanded={expandedFiles === index}
                >
                  {partiallyIgnored ? (
                    <span
                      className="text-amber-600 dark:text-amber-400"
                      title={t('{count} of {total} files skipped', {
                        count: ignoredCount,
                        total: book.files.length,
                      })}
                    >
                      {t('{count} of {total} files', {
                        count: book.files.length - ignoredCount,
                        total: book.files.length,
                      })}
                    </span>
                  ) : (
                    fileCountLabel
                  )}
                </button>
              </div>
              <div className="mt-1 flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
                <ToggleSwitch
                  checked={skipped}
                  onChange={(checked) => setUserIgnored(setBookIgnored(userIgnored, book, checked))}
                  color="emerald"
                  ariaLabel={t('Skip book {number}', { number: index + 1 })}
                  disabled={isSubmitting}
                />
                {t('Skip this book')}
              </div>
              {expandedFiles === index && (
                <ul className="mt-2 max-h-40 overflow-y-auto rounded-md bg-(--bg-soft) px-3 py-2 font-mono text-xs break-all text-zinc-600 dark:text-zinc-300">
                  {book.files.map((file) => (
                    <li key={file}>
                      <div className="flex items-center gap-2 py-0.5">
                        <ToggleSwitch
                          checked={!userIgnored.has(file)}
                          onChange={() => toggleFile(file)}
                          color="emerald"
                          ariaLabel={t('Include {file}', { file })}
                          disabled={isSubmitting}
                        />
                        <span className={userIgnored.has(file) ? 'line-through opacity-60' : ''}>
                          {file}
                        </span>
                      </div>
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
            {plan.ignored.length === 1
              ? t('1 file ignored (not a book format)')
              : t('{count} files ignored (not a book format)', { count: plan.ignored.length })}
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
          &larr; {t('Back')}
        </button>
        <button
          type="button"
          onClick={() => void onConfirm(singleBook ? null : payloadBooks, [...userIgnored])}
          disabled={!canConfirm}
          className="rounded-lg bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isSubmitting ? t('Queuing…') : confirmLabel}
        </button>
      </div>
    </div>
  );
};

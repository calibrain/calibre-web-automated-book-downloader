import { useState } from 'react';

import type { Release } from '../types';
import { formatBytes, ignoredForFormats, type FormatGroup } from '../utils/formatReview';
import { ToggleSwitch } from './shared/ToggleSwitch';

interface FormatReviewPanelProps {
  release: Release;
  groups: FormatGroup[];
  onBack: () => void;
  /** Release-relative paths of the files in every format the user switched off. */
  onConfirm: (ignoredFiles: string[]) => Promise<void>;
  isSubmitting: boolean;
}

export const FormatReviewPanel = ({
  release,
  groups,
  onBack,
  onConfirm,
  isSubmitting,
}: FormatReviewPanelProps) => {
  const [selected, setSelected] = useState<ReadonlySet<string>>(
    new Set(groups.map((group) => group.format)),
  );
  const [expanded, setExpanded] = useState<string | null>(null);

  const toggleFormat = (format: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(format)) next.delete(format);
      else next.add(format);
      return next;
    });
  };

  const fileCount = groups
    .filter((group) => selected.has(group.format))
    .reduce((sum, group) => sum + group.files.length, 0);
  const canConfirm = !isSubmitting && selected.size > 0;

  return (
    <div className="flex flex-col gap-4 px-5 py-4" data-testid="format-review-panel">
      <div>
        <h3 className="text-base font-semibold text-(--text)">Choose formats to download</h3>
        <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
          <span className="font-medium text-(--text)">{release.title}</span> comes in several
          formats. Switch off any you don&rsquo;t want; they won&rsquo;t be filed.
        </p>
      </div>

      <div className="flex flex-col divide-y divide-zinc-200/60 dark:divide-zinc-800/60">
        {groups.map((group) => {
          const included = selected.has(group.format);
          const size = formatBytes(group.totalBytes);
          return (
            <div key={group.format} className={`py-2 ${included ? '' : 'opacity-50'}`}>
              <div className="flex items-center gap-3">
                <ToggleSwitch
                  checked={included}
                  onChange={() => toggleFormat(group.format)}
                  color="emerald"
                  ariaLabel={`Include ${group.format || 'unknown'} format`}
                  disabled={isSubmitting}
                />
                <span className="w-16 text-sm font-medium text-(--text) uppercase">
                  {group.format || 'other'}
                </span>
                <button
                  type="button"
                  onClick={() => setExpanded(expanded === group.format ? null : group.format)}
                  className="hover-surface ml-auto rounded-md px-2 py-1 text-sm text-zinc-500 transition-colors dark:text-zinc-400"
                  aria-expanded={expanded === group.format}
                >
                  {group.files.length} {group.files.length === 1 ? 'file' : 'files'}
                  {size && ` · ${size}`}
                </button>
              </div>
              {expanded === group.format && (
                <ul className="mt-2 max-h-40 overflow-y-auto rounded-md bg-(--bg-soft) px-3 py-2 font-mono text-xs break-all text-zinc-600 dark:text-zinc-300">
                  {group.files.map((file) => (
                    <li key={file.path}>{file.path}</li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>

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
          onClick={() => void onConfirm(ignoredForFormats(groups, selected))}
          disabled={!canConfirm}
          className="rounded-lg bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isSubmitting
            ? 'Queuing…'
            : `Download ${fileCount} ${fileCount === 1 ? 'file' : 'files'}`}
        </button>
      </div>
    </div>
  );
};

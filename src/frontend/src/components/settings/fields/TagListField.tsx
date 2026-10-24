import { useMemo, useRef, useState } from 'react';

import { t } from '../../../i18n';
import type { TagListFieldConfig } from '../../../types/settings';

interface TagListFieldProps {
  field: TagListFieldConfig;
  value: string[];
  onChange: (value: string[]) => void;
  disabled?: boolean;
  requiredTags?: string[]; // Tags that cannot be removed
}

function createTagEntries(tags: string[]): Array<{ key: string; tag: string; tagIndex: number }> {
  const tagCounts = new Map<string, number>();

  return tags.map((tag, tagIndex) => {
    const nextCount = (tagCounts.get(tag) ?? 0) + 1;
    tagCounts.set(tag, nextCount);

    return {
      key: nextCount === 1 ? tag : `${tag}-${nextCount}`,
      tag,
      tagIndex,
    };
  });
}

function normalizeTag(raw: string, normalizeUrls: boolean): string {
  let s = raw.trim();
  if (!s) return '';

  // Strip wrapping quotes from pasted env/JSON style strings.
  if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))) {
    s = s.slice(1, -1).trim();
  }

  if (normalizeUrls) {
    // Avoid special sentinel values.
    if (s.toLowerCase() === 'auto') return '';

    // Basic URL normalization to keep UX friendly; backend also normalizes on save.
    // Only add https:// if it looks like a domain (contains a dot) and has no protocol.
    // This avoids adding prefixes to non-URL values like OIDC scopes (openid, email, etc.)
    if (!s.includes('://') && !s.startsWith('/') && s.includes('.')) {
      s = `https://${s}`;
    }
    s = s.replace(/\/+$/, '');
  }
  return s.trim();
}

export const TagListField = ({
  field,
  value,
  onChange,
  disabled,
  requiredTags,
}: TagListFieldProps) => {
  const isDisabled = disabled ?? false;
  const required = requiredTags ?? [];
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState('');
  const normalizeUrls = field.normalizeUrls ?? true;

  const tags = useMemo(() => (value ?? []).map(String).filter((tag) => tag.trim() !== ''), [value]);
  const tagEntries = useMemo(() => createTagEntries(tags), [tags]);

  const addFromRaw = (raw: string) => {
    if (isDisabled) return;
    const parts = raw
      .split(/[\n,]+/)
      .map((p) => p.trim())
      .filter(Boolean);
    if (parts.length === 0) return;

    const next = [...tags];
    for (const part of parts) {
      const normalized = normalizeTag(part, normalizeUrls);
      if (!normalized) continue;
      if (next.includes(normalized)) continue;
      next.push(normalized);
    }

    if (next.length !== tags.length) {
      onChange(next);
    }
  };

  const isRequired = (tag: string) => required.includes(tag);

  const removeAt = (idx: number) => {
    if (isDisabled || isRequired(tags[idx])) return;
    onChange(tags.filter((_, i) => i !== idx));
  };

  const commitDraft = () => {
    const raw = draft;
    if (!raw.trim()) return;
    addFromRaw(raw);
    setDraft('');
  };

  return (
    <div
      className={`w-full rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-2 text-sm transition-colors focus-within:border-sky-500 focus-within:ring-2 focus-within:ring-sky-500/50 focus-within:outline-hidden ${isDisabled ? 'cursor-not-allowed opacity-60' : 'cursor-text'}`}
    >
      <div className="flex min-h-5 flex-wrap items-center gap-1">
        {tagEntries.map(({ key, tag, tagIndex }) => (
          <span
            key={key}
            className="inline-flex max-w-full items-center gap-1 rounded-md border border-(--border-muted) bg-(--bg) px-2.5 py-1"
            title={tag}
          >
            <span className="max-w-88 truncate">{tag}</span>
            {!isDisabled && !isRequired(tag) && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  removeAt(tagIndex);
                }}
                className="rounded-sm p-0.5 hover:bg-(--hover-surface)"
                aria-label={t('Remove {tag}', { tag })}
              >
                <svg
                  className="h-4 w-4"
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={1.5}
                  stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </span>
        ))}

        {!isDisabled && (
          <input
            ref={inputRef}
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label={field.label || field.placeholder || t('Add item')}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                commitDraft();
                return;
              }

              if (e.key === 'Backspace' && !draft && tags.length > 0) {
                removeAt(tags.length - 1);
              }
            }}
            onBlur={() => commitDraft()}
            placeholder={tags.length === 0 ? field.placeholder : ''}
            className="min-w-16 flex-1 bg-transparent px-1 py-0 outline-hidden"
          />
        )}

        {isDisabled && tags.length === 0 && (
          <span className="opacity-60">{field.placeholder || t('No values')}</span>
        )}
      </div>
    </div>
  );
};

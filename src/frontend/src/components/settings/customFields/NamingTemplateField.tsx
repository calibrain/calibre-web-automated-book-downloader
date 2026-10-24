import { useMemo, useRef } from 'react';

import { t } from '../../../i18n';
import type { TextFieldConfig } from '../../../types/settings';
import {
  buildNamingTemplatePreview,
  NAMING_TEMPLATE_TOKENS,
  resolveWordSeparator,
  type NamingTemplateContent,
  type NamingTemplateMode,
  type NamingTemplateToken,
} from '../../../utils/namingTemplatePreview';
import type { CustomSettingsFieldRendererProps } from './types';

const FIELD_MODES: Record<string, NamingTemplateMode> = {
  TEMPLATE_RENAME: 'filename',
  TEMPLATE_ORGANIZE: 'path',
  TEMPLATE_AUDIOBOOK_RENAME: 'filename',
  TEMPLATE_AUDIOBOOK_ORGANIZE: 'path',
};

const FIELD_CONTENT: Record<string, NamingTemplateContent> = {
  TEMPLATE_RENAME: 'book',
  TEMPLATE_ORGANIZE: 'book',
  TEMPLATE_AUDIOBOOK_RENAME: 'audiobook',
  TEMPLATE_AUDIOBOOK_ORGANIZE: 'audiobook',
};

const isTextField = (field: unknown): field is TextFieldConfig => {
  return Boolean(
    field && typeof field === 'object' && 'type' in field && field.type === 'TextField',
  );
};

const groupTokens = (tokens: NamingTemplateToken[]) => {
  const groups: Array<NamingTemplateToken['group']> = ['Core', 'Universal', 'Files'];
  return groups
    .map((group) => ({
      group,
      tokens: tokens.filter((token) => token.group === group),
    }))
    .filter((entry) => entry.tokens.length > 0);
};

export const NamingTemplateField = ({
  field,
  values,
  onChange,
  isDisabled,
}: CustomSettingsFieldRendererProps) => {
  const inputRef = useRef<HTMLInputElement>(null);
  const boundField = useMemo(
    () =>
      field.boundFields?.find((candidate): candidate is TextFieldConfig => isTextField(candidate)),
    [field.boundFields],
  );

  if (!boundField) {
    return <p className="text-xs opacity-60">{t('Naming template schema is unavailable.')}</p>;
  }

  const rawValue = values[boundField.key];
  const value: string = typeof rawValue === 'string' ? rawValue : '';
  const mode: NamingTemplateMode = FIELD_MODES[boundField.key] ?? 'filename';
  const content: NamingTemplateContent = FIELD_CONTENT[boundField.key] ?? 'book';
  const fieldDisabled = isDisabled || Boolean(boundField.fromEnv);
  const availableTokens = NAMING_TEMPLATE_TOKENS.filter(
    (token) => !token.audiobookOnly || content === 'audiobook',
  );
  const tokenGroups = groupTokens(availableTokens);
  const wordSeparator = resolveWordSeparator(values.NAMING_WORD_SEPARATOR);
  const preview = buildNamingTemplatePreview(value, mode, content, wordSeparator);
  const hasPathSeparatorInFilename = mode === 'filename' && /[\\/]/.test(value);
  const unknownTokenList = preview.unknownTokens.map((token) => `{${token}}`).join(', ');

  const insertToken = (token: string) => {
    if (fieldDisabled) {
      return;
    }

    const insertion = `{${token}}`;
    const input = inputRef.current;
    if (!input) {
      onChange(boundField.key, `${value}${insertion}`);
      return;
    }

    const start = input.selectionStart ?? value.length;
    const end = input.selectionEnd ?? value.length;
    const nextValue = `${value.slice(0, start)}${insertion}${value.slice(end)}`;
    onChange(boundField.key, nextValue);

    window.requestAnimationFrame(() => {
      input.focus();
      const cursor = start + insertion.length;
      input.setSelectionRange(cursor, cursor);
    });
  };

  return (
    <div className="min-w-0 space-y-3">
      <div className="space-y-1.5">
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(event) => onChange(boundField.key, event.target.value)}
          placeholder={boundField.placeholder}
          aria-label={boundField.placeholder || t('Naming Template')}
          maxLength={boundField.maxLength}
          disabled={fieldDisabled}
          className="w-full rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-2 text-sm transition-colors focus:border-sky-500 focus:ring-2 focus:ring-sky-500/50 focus:outline-hidden disabled:cursor-not-allowed disabled:opacity-60"
        />
      </div>

      {(hasPathSeparatorInFilename || preview.unknownTokens.length > 0) && (
        <div className="space-y-1 text-xs text-amber-600 dark:text-amber-400">
          {hasPathSeparatorInFilename && (
            <p>
              {t('Rename templates cannot contain folder separators. Use Path Template instead.')}
            </p>
          )}
          {preview.unknownTokens.length > 0 && (
            <p>
              {preview.unknownTokens.length === 1
                ? t('Unknown variable: {tokens}', { tokens: unknownTokenList })
                : t('Unknown variables: {tokens}', { tokens: unknownTokenList })}
            </p>
          )}
        </div>
      )}

      <div className="w-full rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-2 text-sm leading-relaxed break-words">
        <span className="opacity-60">{t('Preview:')}</span>{' '}
        <code className="font-mono text-(--text)">{preview.value}</code>
      </div>

      <details className="min-w-0">
        <summary className="cursor-pointer text-xs font-semibold text-sky-500 select-none hover:text-sky-400 dark:text-sky-400 dark:hover:text-sky-300">
          {t('Insert variable')}
        </summary>
        <div className="mt-1.5 space-y-3 rounded-lg border border-(--border-muted) bg-(--bg-soft) p-3">
          {tokenGroups.map((group) => (
            <div key={group.group} className="min-w-0">
              <div className="mb-1.5 text-[11px] font-medium tracking-wide text-zinc-500 uppercase dark:text-zinc-400">
                {t(group.group)}
              </div>
              <div className="flex min-w-0 flex-wrap gap-1.5">
                {group.tokens.map((token) => (
                  <button
                    key={token.token}
                    type="button"
                    onClick={() => insertToken(token.token)}
                    disabled={fieldDisabled}
                    title={`${t(token.description)}: ${token.value}`}
                    className="inline-flex min-h-8 max-w-full items-center rounded-md bg-zinc-500/15 px-2.5 py-1 font-mono text-xs transition-colors hover:bg-zinc-500/25 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {`{${token.token}}`}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
};

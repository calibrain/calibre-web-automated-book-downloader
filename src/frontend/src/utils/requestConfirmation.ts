import { t } from '../i18n';
import type { Book, CreateRequestPayload } from '../types';
import { toStringValue } from './objectHelpers';

export const MAX_REQUEST_NOTE_LENGTH = 1000;

const toText = (value: unknown, fallback: string): string => {
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  return fallback;
};

const formatSourceLabel = (value: unknown): string => {
  const source = (toStringValue(value) ?? '').trim();
  if (!source) {
    return t('Unknown source');
  }
  return source
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
};

export const buildSeriesLine = (
  name: string,
  position: number | null,
  count: number | null,
): string => {
  if (!name) return '';
  if (position != null) {
    return count
      ? t('#{position} of {count} in {name}', { position, count, name })
      : t('#{position} in {name}', { position, name });
  }
  return name;
};

export interface RequestConfirmationPreview {
  title: string;
  author: string;
  year: string;
  seriesLine: string;
  preview: string;
  releaseLine: string;
}

export const buildRequestConfirmationPreview = (
  payload: CreateRequestPayload,
): RequestConfirmationPreview => {
  const bookData = payload.book_data || {};
  const releaseData = payload.release_data || {};
  const requestLevel = payload.context?.request_level;

  const seriesLine = buildSeriesLine(
    toText(bookData.series_name, ''),
    typeof bookData.series_position === 'number' ? bookData.series_position : null,
    typeof bookData.series_count === 'number' ? bookData.series_count : null,
  );
  let preview = '';
  if (typeof bookData.preview === 'string') {
    preview = bookData.preview;
  } else if (typeof releaseData.preview === 'string') {
    preview = releaseData.preview;
  }

  return {
    title: toText(bookData.title ?? releaseData.title, t('Untitled')),
    author: toText(bookData.author ?? releaseData.author, t('Unknown author')),
    year: toText(bookData.year ?? releaseData.year, ''),
    seriesLine,
    preview,
    releaseLine:
      requestLevel === 'release'
        ? [
            typeof releaseData.format === 'string' && releaseData.format
              ? releaseData.format.toUpperCase()
              : null,
            typeof releaseData.size === 'string' && releaseData.size ? releaseData.size : null,
            formatSourceLabel(releaseData.source || payload.context?.source),
          ]
            .filter(Boolean)
            .join(' | ')
        : '',
  };
};

export const truncateRequestNote = (
  value: string,
  maxLength: number = MAX_REQUEST_NOTE_LENGTH,
): string => value.slice(0, maxLength);

export const enrichPreviewFromBook = (
  base: RequestConfirmationPreview,
  book: Book,
): RequestConfirmationPreview => {
  const seriesLine = buildSeriesLine(
    book.series_name ?? '',
    book.series_position ?? null,
    book.series_count ?? null,
  );
  if (!seriesLine && !book.year) return base;

  return {
    ...base,
    seriesLine: seriesLine || base.seriesLine,
    year: book.year && !base.year ? book.year : base.year,
  };
};

export const applyRequestNoteToPayload = (
  payload: CreateRequestPayload,
  note: string,
  allowNotes: boolean,
): CreateRequestPayload => {
  const trimmedNote = note.trim();
  const nextPayload: CreateRequestPayload = {
    ...payload,
  };

  if (allowNotes && trimmedNote) {
    nextPayload.note = trimmedNote;
  } else {
    delete nextPayload.note;
  }

  return nextPayload;
};

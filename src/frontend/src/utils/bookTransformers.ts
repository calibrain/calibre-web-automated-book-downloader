import type { Book, LibraryOwnership, Release } from '../types';
import { isRecord, isStringArray } from './objectHelpers';

/**
 * Raw metadata book data from the API (provider responses).
 * Used by both search and single-book endpoints.
 */
export interface MetadataBookData {
  library?: LibraryOwnership; // ownership flags from the library check
  provider: string;
  provider_display_name?: string;
  provider_id: string;
  title: string;
  authors?: string[];
  isbn_10?: string;
  isbn_13?: string;
  cover_url?: string;
  cover_aspect?: 'portrait' | 'square';
  description?: string;
  publisher?: string;
  publish_year?: number;
  language?: string;
  genres?: string[];
  source_url?: string;
  display_fields?: Array<{
    label: string;
    value: string;
    icon?: string;
  }>;
  // Series info
  series_id?: string;
  series_name?: string;
  series_position?: number;
  series_count?: number;
  subtitle?: string;
  search_title?: string;
  search_author?: string;
  titles_by_language?: Record<string, string>;
}

export interface SourceRecordData {
  id: string;
  title: string;
  source: string;
  preview?: string;
  author?: string;
  publisher?: string;
  year?: string | number;
  language?: string;
  format?: string;
  size?: string;
  info?: Record<string, string | string[]>;
  description?: string;
  source_url?: string;
}

interface SourceBackedBookData {
  id: string;
  title: string;
  source: string;
  author?: unknown;
  year?: unknown;
  language?: unknown;
  format?: unknown;
  size?: unknown;
  downloads?: unknown;
  stars?: unknown;
  preview?: unknown;
  publisher?: unknown;
  info?: Record<string, string | string[]>;
  description?: unknown;
  source_url?: unknown;
}

/**
 * Transform raw metadata book data to the frontend Book format.
 * Handles ID generation, author joining, and info object construction.
 */
export function transformMetadataToBook(data: MetadataBookData): Book {
  return {
    id: `${data.provider}:${data.provider_id}`,
    title: data.title,
    author: data.authors?.join(', ') || 'Unknown',
    year: data.publish_year?.toString(),
    language: data.language,
    preview: data.cover_url,
    cover_aspect: data.cover_aspect,
    publisher: data.publisher,
    description: data.description,
    provider: data.provider,
    provider_display_name: data.provider_display_name,
    provider_id: data.provider_id,
    isbn_10: data.isbn_10,
    isbn_13: data.isbn_13,
    genres: data.genres,
    source_url: data.source_url,
    display_fields: data.display_fields,
    series_id: data.series_id,
    series_name: data.series_name,
    series_position: data.series_position,
    series_count: data.series_count,
    subtitle: data.subtitle,
    search_title: data.search_title,
    search_author: data.search_author,
    authors: data.authors,
    titles_by_language: data.titles_by_language,
    library: data.library,
    info: {
      ...(data.isbn_13 && { ISBN: data.isbn_13 }),
      ...(data.isbn_10 && !data.isbn_13 && { ISBN: data.isbn_10 }),
      ...(data.genres && data.genres.length > 0 && { Genres: data.genres }),
    },
  };
}

const toOptionalText = (value: unknown): string | undefined => {
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value);
  }
  return undefined;
};

const humanizeSourceName = (value: string): string => {
  return value
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
};

const parseBookInfo = (value: unknown): Record<string, string | string[]> | undefined => {
  if (!isRecord(value) || Array.isArray(value)) {
    return undefined;
  }

  const info: Record<string, string | string[]> = {};
  for (const [key, entry] of Object.entries(value)) {
    if (typeof entry === 'string') {
      info[key] = entry;
      continue;
    }
    if (isStringArray(entry)) {
      info[key] = entry;
    }
  }

  return Object.keys(info).length > 0 ? info : undefined;
};

const transformSourceBackedDataToBook = (data: SourceBackedBookData): Book => {
  const displayName = humanizeSourceName(data.source);

  return {
    id: data.id,
    title: data.title,
    author: toOptionalText(data.author) || 'Unknown author',
    year: toOptionalText(data.year),
    language: toOptionalText(data.language),
    format: toOptionalText(data.format),
    size: toOptionalText(data.size),
    downloads: typeof data.downloads === 'number' ? data.downloads : undefined,
    stars: typeof data.stars === 'number' ? data.stars : undefined,
    preview: toOptionalText(data.preview),
    publisher: toOptionalText(data.publisher),
    info: data.info,
    description: toOptionalText(data.description),
    source: data.source,
    source_display_name: displayName,
    provider: data.source,
    provider_display_name: displayName,
    provider_id: data.id,
    source_url: toOptionalText(data.source_url),
  };
};

export function transformReleaseToDirectBook(release: Release): Book {
  const extra = release.extra || {};
  return transformSourceBackedDataToBook({
    id: release.source_id,
    title: release.title,
    source: release.source,
    author: extra.author,
    year: extra.year,
    language: release.language || extra.language,
    format: release.format,
    size: release.size,
    downloads: extra.downloads,
    stars: extra.stars,
    preview: extra.preview,
    publisher: extra.publisher,
    info: parseBookInfo(extra.info),
    description: extra.description,
    source_url: release.info_url || release.download_url,
  });
}

export function transformSourceRecordToBook(record: SourceRecordData): Book {
  return transformSourceBackedDataToBook(record);
}

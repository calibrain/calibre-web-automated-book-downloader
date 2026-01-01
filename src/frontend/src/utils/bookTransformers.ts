import { Book } from '../types';

/**
 * Raw metadata book data from the API (provider responses).
 * Used by both search and single-book endpoints.
 */
export interface MetadataBookData {
  provider: string;
  provider_display_name?: string;
  provider_id: string;
  title: string;
  authors?: string[];
  isbn_10?: string;
  isbn_13?: string;
  cover_url?: string;
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
  series_name?: string;
  series_position?: number;
  series_count?: number;
  subtitle?: string;
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
    series_name: data.series_name,
    series_position: data.series_position,
    series_count: data.series_count,
    subtitle: data.subtitle,
    info: {
      ...(data.isbn_13 && { ISBN: data.isbn_13 }),
      ...(data.isbn_10 && !data.isbn_13 && { ISBN: data.isbn_10 }),
      ...(data.genres && data.genres.length > 0 && { Genres: data.genres }),
    },
  };
}

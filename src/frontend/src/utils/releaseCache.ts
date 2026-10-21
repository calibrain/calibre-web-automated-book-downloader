import type { Book, ReleasesResponse } from '../types';

/** The book fields that identify one edition's release search. */
export type ReleaseCacheBook = Pick<Book, 'provider' | 'provider_id' | 'title' | 'language'>;

// Module-level cache for release search results
// Key format: `${provider}:${provider_id}:${language}:${title}:${source}:${contentType}`
// This persists across modal open/close cycles
const releaseCache = new Map<string, ReleasesResponse>();

// A provider can report every edition of a book under one ID - Open Library uses the
// work ID for "The Way of Kings" and its German translation "Der Weg der Könige" alike -
// and each edition searches for releases by its own title and language. Keying on the
// ID alone would serve one edition's cached releases to the other.
function getCacheKey(book: ReleaseCacheBook, source: string, contentType: string): string {
  return [
    book.provider,
    book.provider_id,
    book.language ?? '',
    book.title,
    source,
    contentType,
  ].join(':');
}

// Default cache TTL (5 minutes) - sources can override via column_config.cache_ttl_seconds
const DEFAULT_CACHE_TTL_MS = 5 * 60 * 1000;
const cacheTimestamps = new Map<string, number>();

export function getCachedReleases(
  book: ReleaseCacheBook,
  source: string,
  contentType: string,
): ReleasesResponse | null {
  const key = getCacheKey(book, source, contentType);
  const timestamp = cacheTimestamps.get(key);
  const cached = releaseCache.get(key);

  if (!timestamp || !cached) {
    return null;
  }

  // Use source-specific TTL if available, otherwise default
  const ttlSeconds = cached.column_config?.cache_ttl_seconds;
  const ttlMs = ttlSeconds ? ttlSeconds * 1000 : DEFAULT_CACHE_TTL_MS;

  // Check if cache entry is not expired
  if (Date.now() - timestamp < ttlMs) {
    return cached;
  }

  // Clear expired entry
  releaseCache.delete(key);
  cacheTimestamps.delete(key);

  return null;
}

export function setCachedReleases(
  book: ReleaseCacheBook,
  source: string,
  contentType: string,
  data: ReleasesResponse,
): void {
  const key = getCacheKey(book, source, contentType);
  releaseCache.set(key, data);
  cacheTimestamps.set(key, Date.now());
}

export function invalidateCachedReleases(
  book: ReleaseCacheBook,
  source: string,
  contentType: string,
): void {
  const key = getCacheKey(book, source, contentType);
  releaseCache.delete(key);
  cacheTimestamps.delete(key);
}

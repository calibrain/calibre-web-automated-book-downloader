import type { DownloadReleasePayload } from '../services/api';
import type { Book, ContentType, PackBook, Release } from '../types';

export interface ReleaseDownloadOptions {
  /** Ask post-processing to split the release into one book per subfolder/file. */
  multiBook?: boolean;
  /** The split the user approved in the pack review panel. */
  bookPlan?: PackBook[];
  /** Files from the pack the user chose to skip (release-relative paths). */
  ignoredFiles?: string[];
}

/** Download count for a release, preferring what the release itself reports. */
const releaseDownloads = (book: Book, release: Release): number => {
  if (typeof release.extra?.downloads === 'number') {
    return release.extra.downloads;
  }
  if (typeof book.downloads === 'number' && book.downloads > 0) {
    return book.downloads;
  }
  if (typeof book.extra?.downloads === 'number') {
    return book.extra.downloads;
  }
  if (Array.isArray(book.info?.Downloads) && book.info.Downloads.length > 0) {
    return Number(book.info.Downloads[0]) || 0;
  }
  return 0;
};

/** Build the body for /api/releases/download (and /api/releases/inspect). */
export function buildReleaseDownloadPayload(
  book: Book,
  release: Release,
  releaseContentType: ContentType,
  options: ReleaseDownloadOptions = {},
): DownloadReleasePayload {
  const isManual = book.provider === 'manual';
  const releasePreview =
    typeof release.extra?.preview === 'string' ? release.extra.preview : undefined;
  const releaseAuthor =
    typeof release.extra?.author === 'string' ? release.extra.author : undefined;

  const payload: DownloadReleasePayload = {
    source: release.source,
    source_id: release.source_id,
    title: isManual ? release.title : book.title,
    author: isManual ? releaseAuthor || '' : book.author,
    year: book.year,
    format: release.format,
    size: release.size,
    size_bytes: release.size_bytes,
    downloads: releaseDownloads(book, release),
    download_url: release.download_url,
    protocol: release.protocol,
    indexer: release.indexer,
    seeders: release.seeders,
    extra: release.extra,
    preview: isManual ? releasePreview || undefined : book.preview,
    content_type: releaseContentType,
    series_name: book.series_name,
    series_position: book.series_position,
    subtitle: book.subtitle,
    // From the release, never the book: book.language is the provider's
    // canonical edition, which would mislabel a translated release.
    language: release.language ?? undefined,
  };

  if (options.multiBook || options.bookPlan) {
    payload.multi_book = true;
  }
  if (options.bookPlan) {
    payload.book_plan = options.bookPlan;
  }
  if (options.ignoredFiles && options.ignoredFiles.length > 0) {
    payload.ignored_files = options.ignoredFiles;
  }
  return payload;
}

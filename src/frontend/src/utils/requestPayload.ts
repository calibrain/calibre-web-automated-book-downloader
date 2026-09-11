import type { Book, ContentType, CreateRequestPayload, Release } from '../types';

export const toContentType = (value: string): ContentType => {
  return value.trim().toLowerCase() === 'audiobook' ? 'audiobook' : 'ebook';
};

export const getBrowseSource = (book: Book): string => {
  const source = book.source || book.provider;
  if (source) {
    return source;
  }
  throw new Error(`Book ${book.id} is missing source context`);
};

export const isSourceBackedRequestPayload = (
  payload: CreateRequestPayload | null | undefined,
): boolean => {
  if (!payload) {
    return false;
  }

  const provider =
    typeof payload.book_data?.provider === 'string' ? payload.book_data.provider.trim() : '';
  const source = typeof payload.context?.source === 'string' ? payload.context.source.trim() : '';

  return Boolean(provider) && Boolean(source) && source !== '*' && provider === source;
};

export const buildMetadataBookRequestData = (book: Book, contentType: ContentType) => {
  return {
    title: book.title || 'Unknown title',
    author: book.author || 'Unknown author',
    content_type: contentType,
    provider: book.provider || 'metadata',
    provider_id: book.provider_id || book.id,
    year: book.year,
    preview: book.preview,
    series_name: book.series_name,
    series_position: book.series_position,
    series_count: book.series_count,
    subtitle: book.subtitle,
    source_url: book.source_url,
  };
};

const buildDirectBookRequestData = (book: Book) => {
  const source = getBrowseSource(book);
  return {
    title: book.title || 'Unknown title',
    author: book.author || 'Unknown author',
    content_type: 'ebook' as const,
    provider: source,
    provider_id: book.provider_id || book.id,
    year: book.year,
    format: book.format,
    size: book.size,
    preview: book.preview,
    source,
    source_url: book.source_url,
  };
};

export const buildReleaseDataFromMetadataRelease = (
  book: Book,
  release: Release,
  contentType: ContentType,
) => {
  const isSourceBackedReleaseContext =
    Boolean(book.provider) &&
    book.provider === release.source &&
    (!book.source || book.source === release.source);

  return {
    source: release.source,
    source_id: release.source_id,
    title: book.title || release.title || 'Unknown title',
    author: book.author,
    year: book.year,
    format: release.format,
    size: release.size,
    size_bytes: release.size_bytes,
    download_url: release.download_url,
    protocol: release.protocol,
    indexer: release.indexer,
    seeders: release.seeders,
    extra: release.extra,
    preview: book.preview,
    content_type: contentType,
    series_name: book.series_name,
    series_position: book.series_position,
    series_count: book.series_count,
    subtitle: book.subtitle,
    language: release.language,
    ...(isSourceBackedReleaseContext ? { search_mode: 'direct' as const } : {}),
  };
};

export const buildReleaseDataFromDirectBook = (book: Book) => {
  const source = getBrowseSource(book);
  return {
    source,
    source_id: book.id,
    title: book.title || 'Unknown title',
    author: book.author,
    year: book.year,
    format: book.format,
    size: book.size,
    download_url: book.source_url,
    preview: book.preview,
    content_type: 'ebook' as const,
    // Browsing a source directly means the book record IS the release record.
    language: book.language,
    search_mode: 'direct' as const,
  };
};

export const buildDirectRequestPayload = (book: Book): CreateRequestPayload => {
  const bookData = buildDirectBookRequestData(book);
  const source = getBrowseSource(book);

  // In direct mode, every result already represents a concrete downloadable release.
  // Keep request payloads release-level so admins can approve immediately while still
  // allowing alternate release selection from the same direct record.
  return {
    book_data: bookData,
    release_data: buildReleaseDataFromDirectBook(book),
    context: {
      source,
      content_type: 'ebook',
      request_level: 'release',
    },
  };
};

export const getRequestSuccessMessage = (payload: CreateRequestPayload): string => {
  const bookData = payload.book_data || {};
  const releaseData = payload.release_data || {};
  const title =
    (typeof bookData.title === 'string' && bookData.title.trim()) ||
    (typeof releaseData.title === 'string' && releaseData.title.trim()) ||
    'Untitled';
  return `Request submitted: ${title}`;
};

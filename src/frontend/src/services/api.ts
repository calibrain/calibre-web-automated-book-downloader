import { Book, StatusData, AppConfig, LoginCredentials, AuthResponse, ReleaseSource, ReleasesResponse } from '../types';
import { SettingsResponse, ActionResult, UpdateResult } from '../types/settings';
import { MetadataBookData, transformMetadataToBook } from '../utils/bookTransformers';

const API_BASE = '/api';

// API endpoints
const API = {
  search: `${API_BASE}/search`,
  metadataSearch: `${API_BASE}/metadata/search`,
  info: `${API_BASE}/info`,
  download: `${API_BASE}/download`,
  status: `${API_BASE}/status`,
  cancelDownload: `${API_BASE}/download`,
  setPriority: `${API_BASE}/queue`,
  clearCompleted: `${API_BASE}/queue/clear`,
  config: `${API_BASE}/config`,
  login: `${API_BASE}/auth/login`,
  logout: `${API_BASE}/auth/logout`,
  authCheck: `${API_BASE}/auth/check`,
  settings: `${API_BASE}/settings`,
};

// Custom error class for authentication failures
export class AuthenticationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AuthenticationError';
  }
}

// Utility function for JSON fetch with credentials
async function fetchJSON<T>(url: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(url, {
    ...opts,
    credentials: 'include',  // Enable cookies for session
    headers: {
      'Content-Type': 'application/json',
      ...opts.headers,
    },
  });
  
  if (!res.ok) {
    // Try to parse error message from response body
    let errorMessage = `${res.status} ${res.statusText}`;
    try {
      const errorData = await res.json();
      // Prefer user-friendly 'message' field, fall back to 'error'
      if (errorData.message) {
        errorMessage = errorData.message;
      } else if (errorData.error) {
        errorMessage = errorData.error;
      }
    } catch (e) {
      // Log parse failure for debugging - server may have returned non-JSON (e.g., HTML error page)
      console.warn(`Failed to parse error response from ${url}:`, e instanceof Error ? e.message : e);
    }

    // Throw appropriate error based on status code
    if (res.status === 401) {
      throw new AuthenticationError(errorMessage);
    }

    throw new Error(errorMessage);
  }
  
  return res.json();
}

// API functions
export const searchBooks = async (query: string): Promise<Book[]> => {
  if (!query) return [];
  return fetchJSON<Book[]>(`${API.search}?${query}`);
};

// Metadata search response type (internal)
interface MetadataSearchResponse {
  books: MetadataBookData[];
  provider: string;
  query: string;
  page?: number;
  total_found?: number;
  has_more?: boolean;
}

// Metadata search result with pagination info
export interface MetadataSearchResult {
  books: Book[];
  page: number;
  totalFound: number;
  hasMore: boolean;
}

// Search metadata providers and normalize to Book format
export const searchMetadata = async (
  query: string,
  limit: number = 40,
  sort: string = 'relevance',
  fields: Record<string, string | number | boolean> = {},
  page: number = 1,
  contentType: string = 'ebook'
): Promise<MetadataSearchResult> => {
  const hasFields = Object.values(fields).some(v => v !== '' && v !== false);

  if (!query && !hasFields) {
    return { books: [], page: 1, totalFound: 0, hasMore: false };
  }

  const params = new URLSearchParams();
  if (query) {
    params.set('query', query);
  }
  params.set('limit', String(limit));
  params.set('sort', sort);
  params.set('page', String(page));
  params.set('content_type', contentType);

  // Add custom search field values
  Object.entries(fields).forEach(([key, value]) => {
    if (value !== '' && value !== false) {
      params.set(key, String(value));
    }
  });

  const response = await fetchJSON<MetadataSearchResponse>(`${API.metadataSearch}?${params.toString()}`);

  return {
    books: response.books.map(transformMetadataToBook),
    page: response.page || page,
    totalFound: response.total_found || 0,
    hasMore: response.has_more || false,
  };
};

export const getBookInfo = async (id: string): Promise<Book> => {
  return fetchJSON<Book>(`${API.info}?id=${encodeURIComponent(id)}`);
};

// Get full book details from a metadata provider
export const getMetadataBookInfo = async (provider: string, bookId: string): Promise<Book> => {
  const response = await fetchJSON<MetadataBookData>(
    `${API_BASE}/metadata/book/${encodeURIComponent(provider)}/${encodeURIComponent(bookId)}`
  );

  return transformMetadataToBook(response);
};

export const downloadBook = async (id: string): Promise<void> => {
  await fetchJSON(`${API.download}?id=${encodeURIComponent(id)}`);
};

// Download a specific release (from ReleaseModal)
export const downloadRelease = async (release: {
  source: string;
  source_id: string;
  title: string;
  author?: string;   // Author from metadata provider
  year?: string;     // Year from metadata provider
  format?: string;
  size?: string;
  size_bytes?: number;
  download_url?: string;
  protocol?: string;
  indexer?: string;
  seeders?: number;
  extra?: Record<string, unknown>;
  preview?: string;  // Book cover from metadata provider
}): Promise<void> => {
  await fetchJSON(`${API_BASE}/releases/download`, {
    method: 'POST',
    body: JSON.stringify(release),
  });
};

export const getStatus = async (): Promise<StatusData> => {
  return fetchJSON<StatusData>(API.status);
};

export const cancelDownload = async (id: string): Promise<void> => {
  await fetchJSON(`${API.cancelDownload}/${encodeURIComponent(id)}/cancel`, { method: 'DELETE' });
};

export const clearCompleted = async (): Promise<void> => {
  await fetchJSON(`${API_BASE}/queue/clear`, { method: 'DELETE' });
};

export const getConfig = async (): Promise<AppConfig> => {
  return fetchJSON<AppConfig>(API.config);
};

// Authentication functions
export const login = async (credentials: LoginCredentials): Promise<AuthResponse> => {
  return fetchJSON<AuthResponse>(API.login, {
    method: 'POST',
    body: JSON.stringify(credentials),
  });
};

export const logout = async (): Promise<AuthResponse> => {
  return fetchJSON<AuthResponse>(API.logout, {
    method: 'POST',
  });
};

export const checkAuth = async (): Promise<AuthResponse> => {
  return fetchJSON<AuthResponse>(API.authCheck);
};

// Settings API functions
export const getSettings = async (): Promise<SettingsResponse> => {
  return fetchJSON<SettingsResponse>(API.settings);
};

export const updateSettings = async (
  tabName: string,
  values: Record<string, unknown>
): Promise<UpdateResult> => {
  return fetchJSON<UpdateResult>(`${API.settings}/${tabName}`, {
    method: 'PUT',
    body: JSON.stringify(values),
  });
};

export const executeSettingsAction = async (
  tabName: string,
  actionKey: string,
  currentValues?: Record<string, unknown>
): Promise<ActionResult> => {
  return fetchJSON<ActionResult>(`${API.settings}/${tabName}/action/${actionKey}`, {
    method: 'POST',
    body: currentValues ? JSON.stringify(currentValues) : undefined,
  });
};

// Release source API functions

// Get available release sources from plugin registry
export const getReleaseSources = async (): Promise<ReleaseSource[]> => {
  return fetchJSON<ReleaseSource[]>(`${API_BASE}/release-sources`);
};

// Search for releases of a book
export const getReleases = async (
  provider: string,
  bookId: string,
  source?: string,
  title?: string,
  author?: string,
  expandSearch?: boolean,
  languages?: string[],
  contentType?: string
): Promise<ReleasesResponse> => {
  const params = new URLSearchParams({
    provider,
    book_id: bookId,
  });
  if (source) {
    params.set('source', source);
  }
  if (title) {
    params.set('title', title);
  }
  if (author) {
    params.set('author', author);
  }
  if (expandSearch) {
    params.set('expand_search', 'true');
  }
  if (languages && languages.length > 0) {
    params.set('languages', languages.join(','));
  }
  if (contentType) {
    params.set('content_type', contentType);
  }
  return fetchJSON<ReleasesResponse>(`${API_BASE}/releases?${params.toString()}`);
};

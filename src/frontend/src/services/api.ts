import type {
  Book,
  StatusData,
  AppConfig,
  LoginCredentials,
  AuthResponse,
  ReleaseSource,
  ReleasesResponse,
  RequestPolicyResponse,
  CreateRequestPayload,
  RequestRecord,
  RequestSubmissionResult,
  MetadataProvidersResponse,
  MetadataSearchConfig,
  PackBook,
  InspectReleaseResponse,
} from '../types';
import type {
  ActionResult,
  SettingsField,
  SettingsResponse,
  SettingsTab,
  UpdateResult,
} from '../types/settings';
import { getApiBase, withBasePath } from '../utils/basePath';
import type { MetadataBookData, SourceRecordData } from '../utils/bookTransformers';
import {
  transformMetadataToBook,
  transformReleaseToDirectBook,
  transformSourceRecordToBook,
} from '../utils/bookTransformers';
import { isRecord, toStringValue } from '../utils/objectHelpers';
import type { FulfilAdminRequestBody, RejectAdminRequestBody } from './requestApiHelpers';
import {
  buildAdminRequestActionUrl,
  buildFulfilAdminRequestBody,
  buildRejectAdminRequestBody,
} from './requestApiHelpers';

const API_BASE = getApiBase();

// API endpoints
const API = {
  metadataSearch: `${API_BASE}/metadata/search`,
  metadataConfig: `${API_BASE}/metadata/config`,
  metadataProviders: `${API_BASE}/metadata/providers`,
  status: `${API_BASE}/status`,
  cancelDownload: `${API_BASE}/download`,
  retryDownload: `${API_BASE}/download`,
  setPriority: `${API_BASE}/queue`,
  config: `${API_BASE}/config`,
  login: `${API_BASE}/auth/login`,
  logout: `${API_BASE}/auth/logout`,
  authCheck: `${API_BASE}/auth/check`,
  settings: `${API_BASE}/settings`,
  requestPolicy: `${API_BASE}/request-policy`,
  requests: `${API_BASE}/requests`,
  requestsBatch: `${API_BASE}/requests/batch`,
  adminRequests: `${API_BASE}/admin/requests`,
  activitySnapshot: `${API_BASE}/activity/snapshot`,
  activityDismiss: `${API_BASE}/activity/dismiss`,
  activityDismissMany: `${API_BASE}/activity/dismiss-many`,
  activityHistory: `${API_BASE}/activity/history`,
};

// Custom error class for authentication failures
export class AuthenticationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'AuthenticationError';
  }
}

// Custom error class for request timeouts
class TimeoutError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'TimeoutError';
  }
}

type ApiResponseErrorShape = Error & {
  status: number;
  code?: string;
  requiredMode?: string;
  payload?: Record<string, unknown>;
  // Set only when the server explained itself, so callers can tell a real explanation
  // apart from the `503 SERVICE UNAVAILABLE` placeholder built from the status line.
  serverMessage?: string;
};

class ApiResponseError extends Error {
  status: number;
  code?: string;
  requiredMode?: string;
  payload?: Record<string, unknown>;
  serverMessage?: string;

  constructor(
    message: string,
    params: {
      status: number;
      code?: string;
      requiredMode?: string;
      payload?: Record<string, unknown>;
      serverMessage?: string;
    },
  ) {
    super(message);
    this.name = 'ApiResponseError';
    this.status = params.status;
    this.code = params.code;
    this.requiredMode = params.requiredMode;
    this.payload = params.payload;
    this.serverMessage = params.serverMessage;
  }
}

export const isApiResponseError = (error: unknown): error is ApiResponseErrorShape => {
  return error instanceof ApiResponseError;
};

// The client gave up before the server answered. Distinguishable so callers can report
// the wait rather than guessing at a cause: a search that hits this has told us nothing
// about the network or the mirrors, and saying it did is what issue #1285 was about.
export const isTimeoutError = (error: unknown): error is Error => {
  return error instanceof TimeoutError;
};

const mapApiErrorToActionResult = (error: unknown): ActionResult | null => {
  if (!isApiResponseError(error) || !error.payload) {
    return null;
  }

  const payload = error.payload;
  let message: string | null = null;
  if (typeof payload.message === 'string') {
    message = payload.message;
  } else if (typeof payload.error === 'string') {
    message = payload.error;
  }
  if (!message) {
    return null;
  }

  const details = Array.isArray(payload.details)
    ? payload.details.filter(
        (detail): detail is string => typeof detail === 'string' && detail.trim().length > 0,
      )
    : undefined;

  return {
    success: false,
    message,
    ...(details && details.length > 0 ? { details } : {}),
  };
};

// Default request timeout in milliseconds (30 seconds)
const DEFAULT_TIMEOUT_MS = 30000;

// Release searches can be long-running: a source behind Cloudflare/DDoS-Guard has
// to spin up the bypasser and solve the challenge before any results come back,
// which routinely takes well over the default timeout.
//
// The server bounds them itself (RELEASE_SEARCH_TIMEOUT, reported by /api/config) and
// answers a spent budget with a message naming the real cause. This client abort is only
// the backstop for a server that never answers at all, so it has to fire *after* the
// server's own deadline - a fixed 180s here beat the 300s default, so the accurate
// message was never reachable and raising the setting did nothing. See issue #1285.
//
// The margin has to cover what the server still has to do *after* its budget trips, not
// just the budget itself. The deadline is cooperative: it is handed to the bypasser as a
// cancel flag, and internal_bypasser._CDP_UNWIND_GRACE_SECONDS allows 15s on its own for a
// cancelled solve to close its browser - before the handler has serialized releases, built
// the column config and put bytes on the wire. A 15s margin is entirely spent by that
// unwind, so give it room for the unwind plus the response.
const SEARCH_TIMEOUT_MARGIN_MS = 45000;
const FALLBACK_SEARCH_TIMEOUT_MS = 300000; // search_deadline.DEFAULT_SEARCH_BUDGET_SECONDS
let searchTimeoutMs = FALLBACK_SEARCH_TIMEOUT_MS + SEARCH_TIMEOUT_MARGIN_MS;

// Exported for tests; callers get this applied automatically via getConfig().
export const setSearchTimeoutFromConfig = (budgetSeconds: unknown): void => {
  if (typeof budgetSeconds === 'number' && Number.isFinite(budgetSeconds) && budgetSeconds > 0) {
    searchTimeoutMs = budgetSeconds * 1000 + SEARCH_TIMEOUT_MARGIN_MS;
  }
};

export const getSearchTimeoutMs = (): number => searchTimeoutMs;

// Utility function for JSON fetch with credentials and timeout
async function fetchJSON<T>(
  url: string,
  opts: RequestInit = {},
  timeoutMs: number | null = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController();
  const timeoutId =
    timeoutMs && timeoutMs > 0 ? setTimeout(() => controller.abort(), timeoutMs) : null;
  const headers = new Headers(opts.headers);
  if (!headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  try {
    const res = await fetch(url, {
      ...opts,
      credentials: 'include', // Enable cookies for session
      signal: controller.signal,
      headers,
    });

    if (!res.ok) {
      // Try to parse error message from response body
      let errorMessage = `${res.status} ${res.statusText}`;
      let hasServerMessage = false;
      let errorData: Record<string, unknown> | null = null;
      try {
        const parsed: unknown = await res.json();
        if (isRecord(parsed) && !Array.isArray(parsed)) {
          errorData = parsed;
        }
        // Prefer user-friendly 'message' field, fall back to 'error'. Both must carry
        // actual text: an empty string is not the server explaining itself, and treating
        // it as one suppresses the placeholder below and shows the user a blank toast.
        const explanation = [errorData?.message, errorData?.error].find(
          (candidate): candidate is string =>
            typeof candidate === 'string' && candidate.trim() !== '',
        );
        if (explanation !== undefined) {
          errorMessage = explanation;
          hasServerMessage = true;
        }
      } catch (e) {
        // Log parse failure for debugging - server may have returned non-JSON (e.g., HTML error page)
        console.warn(
          `Failed to parse error response from ${url}:`,
          e instanceof Error ? e.message : e,
        );
      }

      // Provide helpful message for gateway/proxy errors
      if (res.status === 502 || res.status === 503 || res.status === 504) {
        if (!hasServerMessage) {
          errorMessage = `Server unavailable (${res.status}). If using a reverse proxy, check its configuration.`;
        }
      }

      // Throw appropriate error based on status code
      if (res.status === 401) {
        throw new AuthenticationError(errorMessage);
      }

      throw new ApiResponseError(errorMessage, {
        status: res.status,
        serverMessage: hasServerMessage ? errorMessage : undefined,
        code: typeof errorData?.code === 'string' ? errorData.code : undefined,
        requiredMode:
          typeof errorData?.required_mode === 'string' ? errorData.required_mode : undefined,
        payload: errorData || undefined,
      });
    }

    // eslint-disable-next-line @typescript-eslint/no-unsafe-return -- fetch() returns untyped JSON; callers provide the expected response shape at the boundary
    return res.json();
  } catch (error) {
    // Handle abort/timeout errors
    if (error instanceof Error && error.name === 'AbortError') {
      throw new TimeoutError(
        'Request timed out. Check your network connection or proxy configuration.',
      );
    }
    throw error;
  } finally {
    if (timeoutId) {
      clearTimeout(timeoutId);
    }
  }
}

// API functions
export const searchBooks = async (query: string): Promise<Book[]> => {
  if (!query) return [];
  const response = await fetchJSON<ReleasesResponse>(
    `${API_BASE}/releases?source=direct_download&${query}`,
    {},
    searchTimeoutMs,
  );
  return response.releases.map(transformReleaseToDirectBook);
};

// Metadata search response type (internal)
interface MetadataSearchResponse {
  books: MetadataBookData[];
  provider: string;
  query: string;
  page?: number;
  total_found?: number;
  has_more?: boolean;
  source_url?: string;
  source_title?: string;
}

// Metadata search result with pagination info
interface MetadataSearchResult {
  books: Book[];
  page: number;
  totalFound: number;
  hasMore: boolean;
  sourceUrl?: string;
  sourceTitle?: string;
}

export interface DynamicFieldOption {
  value: string;
  label: string;
  group?: string;
  description?: string;
}

export interface BookTargetOption {
  value: string;
  label: string;
  group?: string;
  description?: string;
  checked: boolean;
  writable: boolean;
}

interface BookTargetStateResult {
  changed: boolean;
  selected: boolean;
  deselectedTarget?: string;
}

// Search metadata providers and normalize to Book format
export const searchMetadata = async (
  query: string,
  limit: number = 40,
  sort: string = 'relevance',
  fields: Record<string, string | number | boolean> = {},
  page: number = 1,
  contentType: string = 'ebook',
  provider?: string,
): Promise<MetadataSearchResult> => {
  const hasFields = Object.values(fields).some((v) => v !== '' && v !== false);

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
  if (provider) {
    params.set('provider', provider);
  }

  // Add custom search field values
  Object.entries(fields).forEach(([key, value]) => {
    if (value !== '' && value !== false) {
      params.set(key, String(value));
    }
  });

  const response = await fetchJSON<MetadataSearchResponse>(
    `${API.metadataSearch}?${params.toString()}`,
  );

  return {
    books: response.books.map(transformMetadataToBook),
    page: response.page || page,
    totalFound: response.total_found || 0,
    hasMore: response.has_more || false,
    sourceUrl: response.source_url,
    sourceTitle: response.source_title,
  };
};

export const getMetadataProviders = async (): Promise<MetadataProvidersResponse> => {
  return fetchJSON<MetadataProvidersResponse>(API.metadataProviders);
};

export const getMetadataSearchConfig = async (
  contentType: string = 'ebook',
  provider?: string,
): Promise<MetadataSearchConfig> => {
  const params = new URLSearchParams({
    content_type: contentType,
  });

  if (provider) {
    params.set('provider', provider);
  }

  return fetchJSON<MetadataSearchConfig>(`${API.metadataConfig}?${params.toString()}`);
};

export const fetchFieldOptions = async (
  endpoint: string,
  query?: string,
): Promise<DynamicFieldOption[]> => {
  const normalizedEndpoint =
    endpoint.startsWith('http://') || endpoint.startsWith('https://')
      ? endpoint
      : withBasePath(endpoint);

  const url = new URL(normalizedEndpoint, window.location.origin);
  if (query && query.trim().length > 0) {
    url.searchParams.set('query', query.trim());
  }

  const requestUrl =
    url.origin === window.location.origin ? `${url.pathname}${url.search}` : url.toString();

  const response = await fetchJSON<{ options?: unknown }>(requestUrl);
  if (!Array.isArray(response.options)) {
    return [];
  }

  return parseOptionList(response.options).map(({ value, label, group, description }) => ({
    value,
    label,
    group,
    description,
  }));
};

const parseBaseOption = (
  option: Record<string, unknown>,
): { value: string; label: string; group?: string; description?: string } => {
  const value = toStringValue(option.value) ?? '';
  const label = typeof option.label === 'string' ? option.label : value;
  const group = typeof option.group === 'string' ? option.group : undefined;
  const description = typeof option.description === 'string' ? option.description : undefined;
  return { value, label, group, description };
};

const parseOptionList = (raw: unknown): ReturnType<typeof parseBaseOption>[] => {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
    .map(parseBaseOption)
    .filter((option) => option.value !== '');
};

const parseBookTargetOptions = (raw: unknown): BookTargetOption[] => {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
    .map((item) => {
      const baseOption = parseBaseOption(item);
      return {
        value: baseOption.value,
        label: baseOption.label,
        group: baseOption.group,
        description: baseOption.description,
        checked: item.checked === true,
        writable: item.writable !== false,
      };
    })
    .filter((option) => option.value !== '');
};

export const fetchBookTargetOptionsBatch = async (
  provider: string,
  bookIds: string[],
): Promise<Map<string, BookTargetOption[]>> => {
  const response = await fetchJSON<{ results?: unknown }>(
    `${API_BASE}/metadata/book/${encodeURIComponent(provider)}/targets/batch`,
    {
      method: 'POST',
      body: JSON.stringify({ book_ids: bookIds }),
    },
  );

  const results = new Map<string, BookTargetOption[]>();
  if (isRecord(response.results) && !Array.isArray(response.results)) {
    for (const [bookId, options] of Object.entries(response.results)) {
      results.set(bookId, parseBookTargetOptions(options));
    }
  }
  return results;
};

export const setBookTargetState = async (
  provider: string,
  bookId: string,
  target: string,
  selected: boolean,
): Promise<BookTargetStateResult> => {
  const response = await fetchJSON<{
    changed?: unknown;
    selected?: unknown;
    deselected_target?: unknown;
  }>(
    `${API_BASE}/metadata/book/${encodeURIComponent(provider)}/${encodeURIComponent(bookId)}/targets`,
    {
      method: 'PUT',
      body: JSON.stringify({ target, selected }),
    },
  );

  return {
    changed: response.changed === true,
    selected: response.selected === true,
    deselectedTarget:
      typeof response.deselected_target === 'string' ? response.deselected_target : undefined,
  };
};

export const getSourceRecordInfo = async (source: string, id: string): Promise<Book> => {
  const response = await fetchJSON<SourceRecordData>(
    `${API_BASE}/release-sources/${encodeURIComponent(source)}/records/${encodeURIComponent(id)}`,
  );
  return transformSourceRecordToBook(response);
};

// Get full book details from a metadata provider
export const getMetadataBookInfo = async (provider: string, bookId: string): Promise<Book> => {
  const response = await fetchJSON<MetadataBookData>(
    `${API_BASE}/metadata/book/${encodeURIComponent(provider)}/${encodeURIComponent(bookId)}`,
  );

  return transformMetadataToBook(response);
};

// Download a specific release (from ReleaseModal)
export type DownloadReleasePayload = {
  source: string;
  source_id: string;
  title: string;
  author?: string; // Author from metadata provider
  year?: string; // Year from metadata provider
  format?: string;
  size?: string;
  size_bytes?: number;
  download_url?: string;
  protocol?: string;
  indexer?: string;
  seeders?: number;
  extra?: Record<string, unknown>;
  preview?: string; // Book cover from metadata provider
  content_type?: string; // "ebook" or "audiobook" - for directory routing
  series_name?: string;
  series_position?: number;
  subtitle?: string;
  language?: string; // Release language code, for the {Language} naming variable
  search_author?: string;
  search_mode?: 'direct' | 'universal';
  multi_book?: boolean; // Split a multi-book pack into one book per subfolder/file
  book_plan?: PackBook[]; // The split the user approved before download
};

/** Inspect a release's file list before download (same body as downloadRelease). */
export const inspectRelease = async (
  release: DownloadReleasePayload,
): Promise<InspectReleaseResponse> => {
  return fetchJSON<InspectReleaseResponse>(`${API_BASE}/releases/inspect`, {
    method: 'POST',
    body: JSON.stringify(release),
  });
};

export const downloadRelease = async (
  release: DownloadReleasePayload,
  onBehalfOfUserId?: number,
): Promise<void> => {
  const payload =
    typeof onBehalfOfUserId === 'number'
      ? { ...release, on_behalf_of_user_id: onBehalfOfUserId }
      : release;

  await fetchJSON(`${API_BASE}/releases/download`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
};

export const getStatus = async (): Promise<StatusData> => {
  return fetchJSON<StatusData>(API.status);
};

export const getActivitySnapshot = async (): Promise<ActivitySnapshotResponse> => {
  return fetchJSON<ActivitySnapshotResponse>(API.activitySnapshot);
};

export const dismissActivityItem = async (payload: ActivityDismissPayload): Promise<void> => {
  await fetchJSON(API.activityDismiss, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
};

export const dismissManyActivityItems = async (items: ActivityDismissPayload[]): Promise<void> => {
  await fetchJSON(API.activityDismissMany, {
    method: 'POST',
    body: JSON.stringify({ items }),
  });
};

export const listActivityHistory = async (
  limit: number = 50,
  offset: number = 0,
): Promise<ActivityHistoryItem[]> => {
  const params = new URLSearchParams();
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  return fetchJSON<ActivityHistoryItem[]>(`${API.activityHistory}?${params.toString()}`);
};

export const clearActivityHistory = async (): Promise<void> => {
  await fetchJSON(API.activityHistory, { method: 'DELETE' });
};

export const cancelDownload = async (id: string): Promise<void> => {
  await fetchJSON(`${API.cancelDownload}/${encodeURIComponent(id)}/cancel`, { method: 'DELETE' });
};

export const retryDownload = async (id: string): Promise<void> => {
  await fetchJSON(`${API.retryDownload}/${encodeURIComponent(id)}/retry`, { method: 'POST' });
};

export const getConfig = async (): Promise<AppConfig> => {
  const config = await fetchJSON<AppConfig>(API.config);
  setSearchTimeoutFromConfig(config.release_search_timeout);
  return config;
};

interface ActivityDismissedItem {
  item_type: 'download' | 'request';
  item_key: string;
}

interface ActivitySnapshotResponse {
  status: StatusData;
  requests: RequestRecord[];
  dismissed: ActivityDismissedItem[];
}

export interface ActivityDismissPayload {
  item_type: 'download' | 'request';
  item_key: string;
}

export interface ActivityHistoryItem {
  id: string;
  user_id: number;
  item_type: 'download' | 'request';
  item_key: string;
  dismissed_at: string;
  snapshot: Record<string, unknown> | null;
  origin: 'direct' | 'request' | 'requested' | null;
  final_status: string | null;
  terminal_at: string | null;
  request_id: number | null;
  source_id: string | null;
}

export const fetchRequestPolicy = async (): Promise<RequestPolicyResponse> => {
  return fetchJSON<RequestPolicyResponse>(API.requestPolicy);
};

export const createRequests = async (
  payloads: CreateRequestPayload[],
): Promise<RequestSubmissionResult[]> => {
  return fetchJSON<RequestSubmissionResult[]>(API.requestsBatch, {
    method: 'POST',
    body: JSON.stringify({ requests: payloads }),
  });
};

export const cancelRequest = async (id: number): Promise<RequestRecord> => {
  return fetchJSON<RequestRecord>(`${API.requests}/${encodeURIComponent(String(id))}`, {
    method: 'DELETE',
  });
};

export const fulfilAdminRequest = async (
  id: number,
  body: FulfilAdminRequestBody = {},
): Promise<RequestRecord> => {
  return fetchJSON<RequestRecord>(buildAdminRequestActionUrl(API.adminRequests, id, 'fulfil'), {
    method: 'POST',
    body: JSON.stringify(buildFulfilAdminRequestBody(body)),
  });
};

export const rejectAdminRequest = async (
  id: number,
  body: RejectAdminRequestBody = {},
): Promise<RequestRecord> => {
  return fetchJSON<RequestRecord>(buildAdminRequestActionUrl(API.adminRequests, id, 'reject'), {
    method: 'POST',
    body: JSON.stringify(buildRejectAdminRequestBody(body)),
  });
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

export const getSettingsTab = async (tabName: string): Promise<SettingsTab> => {
  return fetchJSON<SettingsTab>(`${API.settings}/${tabName}`);
};

export const updateSettings = async (
  tabName: string,
  values: Record<string, unknown>,
): Promise<UpdateResult> => {
  return fetchJSON<UpdateResult>(`${API.settings}/${tabName}`, {
    method: 'PUT',
    body: JSON.stringify(values),
  });
};

export const executeSettingsAction = async (
  tabName: string,
  actionKey: string,
  currentValues?: Record<string, unknown>,
): Promise<ActionResult> => {
  try {
    return await fetchJSON<ActionResult>(`${API.settings}/${tabName}/action/${actionKey}`, {
      method: 'POST',
      body: currentValues ? JSON.stringify(currentValues) : undefined,
    });
  } catch (error) {
    const mapped = mapApiErrorToActionResult(error);
    if (mapped) {
      return mapped;
    }
    throw error;
  }
};

// Onboarding API functions

export interface OnboardingStepCondition {
  field: string;
  value: unknown;
  notEmpty?: boolean;
}

export interface OnboardingFieldGroup {
  id: string;
  title: string;
  description?: string;
  fieldKeys: string[];
  defaultOpen?: boolean;
}

export interface OnboardingStep {
  id: string;
  title: string;
  tab: string;
  fields: SettingsField[];
  showWhen?: OnboardingStepCondition[]; // Array of conditions (all must be true)
  optional?: boolean;
  fieldGroups?: OnboardingFieldGroup[];
}

interface OnboardingConfig {
  steps: OnboardingStep[];
  values: Record<string, unknown>;
  complete: boolean;
}

export const getOnboarding = async (): Promise<OnboardingConfig> => {
  return fetchJSON<OnboardingConfig>(`${API_BASE}/onboarding`);
};

export const saveOnboarding = async (
  values: Record<string, unknown>,
): Promise<{ success: boolean; message: string }> => {
  return fetchJSON<{ success: boolean; message: string }>(`${API_BASE}/onboarding`, {
    method: 'POST',
    body: JSON.stringify(values),
  });
};

export const skipOnboarding = async (): Promise<{ success: boolean; message: string }> => {
  return fetchJSON<{ success: boolean; message: string }>(`${API_BASE}/onboarding/skip`, {
    method: 'POST',
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
  contentType?: string,
  manualQuery?: string,
  indexers?: string[],
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
  if (manualQuery) {
    params.set('manual_query', manualQuery);
  }
  if (indexers && indexers.length > 0) {
    params.set('indexers', indexers.join(','));
  }
  // Let the backend control timeouts for release searches (can be long-running).
  return fetchJSON<ReleasesResponse>(`${API_BASE}/releases?${params.toString()}`, {}, null);
};

// Admin user management API

export type AdminAuthSource = 'builtin' | 'oidc' | 'proxy' | 'cwa';

export interface AdminUserEditCapabilities {
  authSource: AdminAuthSource;
  canSetPassword: boolean;
  canEditRole: boolean;
  canEditEmail: boolean;
  canEditDisplayName: boolean;
}

export interface AdminUser {
  id: number;
  username: string;
  email: string | null;
  display_name: string | null;
  role: string;
  auth_source: AdminAuthSource;
  is_active: boolean;
  oidc_subject: string | null;
  created_at: string;
  edit_capabilities: AdminUserEditCapabilities;
  settings?: Record<string, unknown>;
}

interface SelfUserEditContext {
  user: AdminUser;
  deliveryPreferences: DeliveryPreferencesResponse | null;
  searchPreferences: DeliveryPreferencesResponse | null;
  notificationPreferences: DeliveryPreferencesResponse | null;
  userOverridableKeys: string[];
  visibleUserSettingsSections?: string[];
}

export const getAdminUsers = async (): Promise<AdminUser[]> => {
  return fetchJSON<AdminUser[]>(`${API_BASE}/admin/users`);
};

export const getAdminUser = async (userId: number): Promise<AdminUser> => {
  return fetchJSON<AdminUser>(`${API_BASE}/admin/users/${userId}`);
};

export const createAdminUser = async (data: {
  username: string;
  password: string;
  email?: string;
  display_name?: string;
  role?: string;
}): Promise<AdminUser> => {
  return fetchJSON<AdminUser>(`${API_BASE}/admin/users`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
};

export const updateAdminUser = async (
  userId: number,
  data: Partial<Pick<AdminUser, 'role' | 'email' | 'display_name'>> & {
    password?: string;
    settings?: Record<string, unknown>;
  },
): Promise<AdminUser> => {
  return fetchJSON<AdminUser>(`${API_BASE}/admin/users/${userId}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
};

export const deleteAdminUser = async (userId: number): Promise<{ success: boolean }> => {
  return fetchJSON<{ success: boolean }>(`${API_BASE}/admin/users/${userId}`, {
    method: 'DELETE',
  });
};

interface CwaUserSyncResult {
  success: boolean;
  message: string;
  created: number;
  updated: number;
  total: number;
}

export const syncAdminCwaUsers = async (): Promise<CwaUserSyncResult> => {
  return fetchJSON<CwaUserSyncResult>(`${API_BASE}/admin/users/sync-cwa`, {
    method: 'POST',
  });
};

export interface DownloadDefaults {
  BOOKS_OUTPUT_MODE: string;
  DESTINATION: string;
  DESTINATION_AUDIOBOOK: string;
  BOOKLORE_LIBRARY_ID: string;
  BOOKLORE_PATH_ID: string;
  EMAIL_RECIPIENT: string;
  OIDC_ADMIN_GROUP: string;
  OIDC_USE_ADMIN_GROUP: boolean;
  OIDC_AUTO_PROVISION: boolean;
}

export const getDownloadDefaults = async (): Promise<DownloadDefaults> => {
  return fetchJSON<DownloadDefaults>(`${API_BASE}/admin/download-defaults`);
};

export interface DeliveryPreferencesResponse {
  tab: string;
  keys: string[];
  fields: SettingsField[];
  globalValues: Record<string, unknown>;
  userOverrides: Record<string, unknown>;
  effective: Record<string, { value: unknown; source: string }>;
}

export const getAdminDeliveryPreferences = async (
  userId: number,
): Promise<DeliveryPreferencesResponse> => {
  return fetchJSON<DeliveryPreferencesResponse>(
    `${API_BASE}/admin/users/${userId}/delivery-preferences`,
  );
};

export const getAdminSearchPreferences = async (
  userId: number,
): Promise<DeliveryPreferencesResponse> => {
  return fetchJSON<DeliveryPreferencesResponse>(
    `${API_BASE}/admin/users/${userId}/search-preferences`,
  );
};

export const getAdminNotificationPreferences = async (
  userId: number,
): Promise<DeliveryPreferencesResponse> => {
  return fetchJSON<DeliveryPreferencesResponse>(
    `${API_BASE}/admin/users/${userId}/notification-preferences`,
  );
};

export const testAdminUserNotificationPreferences = async (
  userId: number,
  routes: Array<Record<string, unknown>>,
): Promise<ActionResult> => {
  try {
    return await fetchJSON<ActionResult>(
      `${API_BASE}/admin/users/${userId}/notification-preferences/test`,
      {
        method: 'POST',
        body: JSON.stringify({ USER_NOTIFICATION_ROUTES: routes }),
      },
    );
  } catch (error) {
    const mapped = mapApiErrorToActionResult(error);
    if (mapped) {
      return mapped;
    }
    throw error;
  }
};

export const testSelfNotificationPreferences = async (
  routes: Array<Record<string, unknown>>,
): Promise<ActionResult> => {
  try {
    return await fetchJSON<ActionResult>(`${API_BASE}/users/me/notification-preferences/test`, {
      method: 'POST',
      body: JSON.stringify({ USER_NOTIFICATION_ROUTES: routes }),
    });
  } catch (error) {
    const mapped = mapApiErrorToActionResult(error);
    if (mapped) {
      return mapped;
    }
    throw error;
  }
};

interface SettingsOverrideUserDetail {
  userId: number;
  username: string;
  value: unknown;
}

interface SettingsOverrideKeySummary {
  count: number;
  users: SettingsOverrideUserDetail[];
}

interface SettingsOverridesSummaryResponse {
  tab: string;
  keys: Record<string, SettingsOverrideKeySummary>;
}

export const getAdminSettingsOverridesSummary = async (
  tabName: string,
): Promise<SettingsOverridesSummaryResponse> => {
  return fetchJSON<SettingsOverridesSummaryResponse>(
    `${API_BASE}/admin/settings/overrides-summary?tab=${encodeURIComponent(tabName)}`,
  );
};

export const getSelfUserEditContext = async (): Promise<SelfUserEditContext> => {
  return fetchJSON<SelfUserEditContext>(`${API_BASE}/users/me/edit-context`);
};

export const updateSelfUser = async (
  data: Partial<Pick<AdminUser, 'email' | 'display_name'>> & {
    password?: string;
    settings?: Record<string, unknown>;
  },
): Promise<AdminUser> => {
  return fetchJSON<AdminUser>(`${API_BASE}/users/me`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
};

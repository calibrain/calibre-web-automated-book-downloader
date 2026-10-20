// Search mode constants and type
export const SEARCH_MODE = {
  DIRECT: 'direct',
  UNIVERSAL: 'universal',
} as const;

export type SearchMode = (typeof SEARCH_MODE)[keyof typeof SEARCH_MODE];

// Display field for metadata cards (provider-specific info like ratings, pages, etc.)
export interface DisplayField {
  label: string; // e.g., "Rating", "Pages", "Readers"
  value: string; // e.g., "4.5", "496", "8,041"
  icon?: string; // Icon name: "star", "book", "users", "editions"
}

// Book data types
export interface Book {
  id: string;
  request_id?: number;
  title: string;
  author: string;
  year?: string;
  language?: string;
  format?: string;
  size?: string;
  preview?: string;
  publisher?: string;
  info?: Record<string, string | string[]>;
  description?: string;
  download_path?: string;
  progress?: number;
  status_message?: string; // Detailed status message (e.g., "Trying Libgen (2/5)")
  added_time?: number; // Timestamp when added to queue
  content_type?: string; // "ebook", "audiobook", or related book subtype
  library?: LibraryOwnership; // "already in your library" flags from the library check
  source?: string; // Release source handler (e.g., "direct_download", "prowlarr")
  source_display_name?: string; // Human-readable source name (e.g., "Direct Download")
  // Metadata provider fields (used in universal search mode)
  provider?: string; // e.g., 'hardcover', 'openlibrary'
  provider_display_name?: string; // e.g., 'Hardcover', 'Open Library'
  provider_id?: string; // ID in provider's system
  isbn_10?: string;
  isbn_13?: string;
  genres?: string[];
  source_url?: string; // Link to book on provider's site
  display_fields?: DisplayField[]; // Provider-specific display data
  cover_aspect?: 'portrait' | 'square'; // Cover art aspect ratio hint
  // Series info (if book is part of a series)
  series_id?: string; // Provider-specific series ID
  series_name?: string; // Name of the series
  series_position?: number; // This book's position (e.g., 3, 1.5 for novellas)
  series_count?: number; // Total books in the series
  subtitle?: string;
  search_title?: string;
  search_author?: string;
  authors?: string[];
  titles_by_language?: Record<string, string>;
  username?: string;
  retry_available?: boolean;
  downloads?: number;
  stars?: number;
  extra?: Record<string, unknown>;
}

/**
 * Extract download count from a book's data.
 * Checks both the direct `downloads` field and the `extra.downloads` fallback.
 */
export function getDownloadsCount(book: Book): number | null {
  if (book.downloads != null && book.downloads > 0) {
    return book.downloads;
  }
  const extraDownloads = book.extra?.downloads;
  if (extraDownloads != null && typeof extraDownloads === 'number' && extraDownloads > 0) {
    return extraDownloads;
  }
  return null;
}

/**
 * Extract star rating from a book's data.
 * Checks both the direct `stars` field and the `extra.stars` fallback.
 */
export function getStarCount(book: Book): number | null {
  if (book.stars != null && book.stars > 0) {
    return book.stars;
  }
  const extraStars = book.extra?.stars;
  if (extraStars != null && typeof extraStars === 'number' && extraStars > 0) {
    return extraStars;
  }
  return null;
}

// Status response types
export interface StatusData {
  queued?: Record<string, Book>;
  resolving?: Record<string, Book>;
  locating?: Record<string, Book>;
  downloading?: Record<string, Book>;
  complete?: Record<string, Book>;
  error?: Record<string, Book>;
  cancelled?: Record<string, Book>;
}

// Button states
export type ButtonState =
  | 'download'
  | 'queued'
  | 'resolving'
  | 'locating'
  | 'downloading'
  | 'complete'
  | 'error'
  | 'blocked';

export interface ButtonStateInfo {
  text: string;
  state: ButtonState;
  progress?: number; // Download progress 0-100
}

// Language option
export interface Language {
  code: string;
  language: string;
}

export interface AdvancedFilterState {
  isbn: string;
  author: string;
  title: string;
  lang: string[];
  sort: string;
  content: string;
  formats: string[];
}

// Toast notification
export interface Toast {
  id: string;
  message: string;
  type: 'success' | 'error' | 'info';
}

// Sort option for dropdowns
export interface SortOption {
  value: string;
  label: string;
  group?: string;
}

// Search field types (mirror backend search field types)
type SearchFieldType =
  | 'TextSearchField'
  | 'NumberSearchField'
  | 'SelectSearchField'
  | 'CheckboxSearchField'
  | 'DynamicSelectSearchField';

interface SearchFieldBase {
  key: string;
  label: string;
  type: SearchFieldType;
  placeholder?: string;
  description?: string;
}

export interface TextSearchField extends SearchFieldBase {
  type: 'TextSearchField';
  suggestions_endpoint?: string;
  suggestions_min_query_length?: number;
}

export interface NumberSearchField extends SearchFieldBase {
  type: 'NumberSearchField';
  min?: number;
  max?: number;
  step?: number;
}

export interface SelectSearchField extends SearchFieldBase {
  type: 'SelectSearchField';
  options: SortOption[];
}

export interface CheckboxSearchField extends SearchFieldBase {
  type: 'CheckboxSearchField';
  default?: boolean;
}

export interface DynamicSelectSearchField extends SearchFieldBase {
  type: 'DynamicSelectSearchField';
  options_endpoint: string;
}

export type MetadataSearchField =
  | TextSearchField
  | NumberSearchField
  | SelectSearchField
  | CheckboxSearchField
  | DynamicSelectSearchField;

export type QueryTargetSource = 'general' | 'manual' | 'direct-field' | 'provider-field';

export interface QueryTargetOption {
  key: string;
  label: string;
  description?: string;
  source: QueryTargetSource;
  field?: MetadataSearchField;
}

// App configuration
// Content type for search (ebook vs audiobook)
export type ContentType = 'ebook' | 'audiobook';

export type RequestPolicyMode = 'download' | 'request_release' | 'request_book' | 'blocked';

export interface RequestPolicyDefaults {
  ebook: RequestPolicyMode;
  audiobook: RequestPolicyMode;
}

export interface RequestPolicySourceMode {
  source: string;
  supported_content_types: string[];
  browse_results_are_releases?: boolean;
  modes: Record<string, RequestPolicyMode>;
}

export interface RequestPolicyResponse {
  requests_enabled: boolean;
  is_admin: boolean;
  allow_notes: boolean;
  defaults: RequestPolicyDefaults;
  rules: Array<Record<string, unknown>>;
  source_modes: RequestPolicySourceMode[];
}

export interface RequestContextPayload {
  source: string;
  content_type: ContentType;
  request_level: 'book' | 'release';
}

export interface CreateRequestPayload {
  book_data: Record<string, unknown>;
  release_data?: Record<string, unknown> | null;
  note?: string;
  on_behalf_of_user_id?: number;
  context: RequestContextPayload;
}

export interface RequestRecord {
  id: number;
  user_id: number;
  status: 'pending' | 'fulfilled' | 'rejected' | 'cancelled';
  delivery_state?:
    | 'none'
    | 'queued'
    | 'resolving'
    | 'locating'
    | 'downloading'
    | 'complete'
    | 'error'
    | 'cancelled';
  delivery_updated_at?: string | null;
  last_failure_reason?: string | null;
  source_hint: string | null;
  content_type: ContentType;
  request_level: 'book' | 'release';
  policy_mode: RequestPolicyMode;
  book_data: Record<string, unknown> | null;
  release_data: Record<string, unknown> | null;
  note: string | null;
  admin_note: string | null;
  reviewed_by: number | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
  username?: string;
}

export interface QueuedDownloadResult {
  kind: 'download';
  status: 'queued';
  priority: number;
  title: string;
  source: string;
  source_id: string | null;
  content_type?: ContentType;
}

export type RequestSubmissionResult = RequestRecord | QueuedDownloadResult;

/** How the library holds a book, per format: owned outright, or inside a collection. */
export type LibraryHolding = 'owned' | 'collection';

export interface LibraryOwnership {
  ebook?: LibraryHolding | null;
  audiobook?: LibraryHolding | null;
}

export type BooksOutputMode = 'folder' | 'booklore' | 'email';

export interface AppConfig {
  calibre_web_url: string;
  audiobook_library_url: string;
  search_page_title: string;
  debug: boolean;
  build_version: string;
  release_version: string;
  book_languages: Language[];
  default_language: string[];
  default_content_type: ContentType;
  supported_formats: string[];
  supported_audiobook_formats: string[]; // Audiobook formats (m4b, mp3)
  search_mode: SearchMode;
  metadata_sort_options: SortOption[];
  metadata_search_fields: MetadataSearchField[];
  default_release_source?: string; // Default tab in ReleaseModal (e.g., 'direct_download')
  default_release_source_audiobook?: string; // Default tab in ReleaseModal for audiobooks
  show_release_source_links: boolean;
  show_combined_selector: boolean;
  force_combined_search: boolean;
  books_output_mode: BooksOutputMode;
  auto_open_downloads_sidebar: boolean; // Auto-open sidebar when download is queued
  hardcover_auto_remove_on_download: boolean; // Auto-remove from active Hardcover list on download
  download_to_browser_content_types: string[]; // Auto-download completed files to browser for selected content types
  release_search_timeout: number; // Server-side budget for one release search, in seconds
  settings_enabled: boolean; // Whether config directory is mounted and writable
  onboarding_complete: boolean; // Whether the user has completed initial setup
  default_sort: string; // Default sort for direct mode
  metadata_default_sort: string; // Default sort for universal mode (from metadata provider)
}

export interface MetadataProviderSummary {
  name: string;
  display_name: string;
  requires_auth: boolean;
  enabled: boolean;
  available: boolean;
}

export interface MetadataProvidersResponse {
  providers: MetadataProviderSummary[];
  configured_provider: string | null;
  configured_provider_audiobook: string | null;
  configured_provider_combined: string | null;
}

export interface MetadataCapability {
  key: string;
  field_key?: string;
  sort?: string;
}

export interface MetadataSearchConfig {
  provider: string | null;
  display_name: string | null;
  enabled: boolean;
  available: boolean;
  search_fields: MetadataSearchField[];
  capabilities: MetadataCapability[];
  sort_options: SortOption[];
  default_sort: string;
}

// Authentication types
export interface LoginCredentials {
  username: string;
  password: string;
  remember_me: boolean;
}

export interface AuthResponse {
  success?: boolean;
  authenticated?: boolean;
  auth_required?: boolean;
  auth_mode?: string;
  is_admin?: boolean;
  username?: string;
  display_name?: string | null;
  error?: string;
  logout_url?: string;
  oidc_button_label?: string;
  hide_local_auth?: boolean;
  oidc_auto_redirect?: boolean;
}

export interface ActingAsUserSelection {
  id: number;
  username: string;
  displayName: string | null;
}

export const isMetadataBook = (
  book: Book,
): book is Book & {
  provider: string;
  provider_id: string;
} => {
  return Boolean(book.provider && book.provider_id) && book.provider !== book.source;
};

// Release source types (from plugin system)
export interface ReleaseSource {
  name: string; // e.g., 'direct_download', 'prowlarr'
  display_name: string; // e.g., 'Direct Download', 'Prowlarr'
  enabled: boolean; // Whether the source is available for use
  supported_content_types?: string[]; // Content types this source supports (e.g., ['ebook', 'audiobook'])
  browse_results_are_releases?: boolean;
}

// Column schema types for plugin-driven release list UI
export type ColumnRenderType =
  | 'text'
  | 'badge'
  | 'tags'
  | 'size'
  | 'number'
  | 'peers'
  | 'indexer_protocol'
  | 'flag_icon'
  | 'format_content_type';
export type ColumnAlign = 'left' | 'center' | 'right';

export interface ColumnColorHint {
  type: 'map' | 'static'; // 'map' uses colorMaps.ts, 'static' is a fixed Tailwind class
  value: string; // Map name ('format', 'language') or Tailwind class
}

export interface ColumnSchema {
  key: string; // Data path (e.g., 'format', 'extra.language')
  label: string; // Accessibility label
  render_type: ColumnRenderType;
  align: ColumnAlign;
  width: string; // CSS width (e.g., '80px')
  hide_mobile: boolean; // Hide on small screens
  color_hint?: ColumnColorHint | null;
  fallback: string; // Value when data is missing
  uppercase: boolean; // Force uppercase display
  sortable?: boolean; // Show in sort dropdown (opt-in)
  sort_key?: string; // Field to sort by (defaults to key if not specified)
}

// Leading cell config - what to show in the left-most position of each row
export type LeadingCellType = 'thumbnail' | 'badge' | 'none';

export interface LeadingCellConfig {
  type: LeadingCellType;
  key?: string; // Field path for data (e.g., 'extra.preview' or 'extra.download_type')
  color_hint?: ColumnColorHint; // For badge type - maps values to colors
  uppercase?: boolean; // Force uppercase for badge text
}

export interface ExtraSortOption {
  label: string; // Display label in the sort dropdown
  sort_key: string; // Field to sort by on the Release object
  default_direction?: 'asc' | 'desc'; // Which way "best first" runs (defaults to desc)
}

export interface SourceActionButton {
  label: string; // Button text (e.g., "Refresh search")
  action: string; // Action type: "expand" triggers expand_search
}

export interface ReleaseColumnConfig {
  columns: ColumnSchema[];
  grid_template: string; // CSS grid-template-columns for dynamic section
  leading_cell?: LeadingCellConfig; // Defaults to thumbnail from extra.preview
  online_servers?: string[]; // For IRC: list of currently online server nicks
  available_indexers?: string[]; // For Prowlarr: list of all enabled indexer names
  default_indexers?: string[]; // For Prowlarr: indexers selected in settings (pre-selected in filter)
  cache_ttl_seconds?: number; // How long to cache results (default: 300 = 5 min)
  supported_filters?: string[]; // Which filters this source supports: ["format", "language", "indexer"]
  extra_sort_options?: ExtraSortOption[]; // Additional sort options not tied to a column
  action_button?: SourceActionButton; // Custom action button (replaces default expand search)
}

// A downloadable release from any source
export interface Release {
  source: string; // Source plugin name
  source_id: string; // ID within that source
  title: string;
  format?: string; // epub, pdf, mobi, etc.
  language?: string; // ISO 639-1 code (e.g., "en", "de", "fr")
  size?: string; // Human-readable size
  size_bytes?: number; // Size in bytes for sorting
  download_url?: string;
  info_url?: string; // Link to release info page (e.g., tracker page) - makes title clickable
  protocol?: 'http' | 'torrent' | 'nzb' | 'dcc';
  indexer?: string; // Display name for the source/indexer
  seeders?: number; // For torrents
  peers?: string; // For torrents: "seeders/leechers" display string
  content_type?: string; // "ebook", "audiobook", or "book"
  extra?: Record<string, unknown>; // Source-specific metadata
}

// Search info returned by release sources
export interface SourceSearchInfo {
  search_type: 'isbn' | 'title_author' | 'categories' | 'expanded' | 'manual' | 'query';
  total_results?: number | string | null;
  filtered_out_count?: number;
}

// Response from /api/releases endpoint
/** One book split out of a multi-book pack release, files as release-relative paths. */
export interface PackBook {
  title: string;
  series_position: number | null;
  year: number | null;
  files: string[];
}

export interface PackPlan {
  is_pack: boolean;
  books: PackBook[];
  ignored: string[];
}

export interface InspectReleaseResponse {
  inspected: boolean;
  reason: string | null;
  files: { path: string; size: number | null }[];
  plan: PackPlan | null;
}

export interface ReleasesResponse {
  releases: Release[];
  book: {
    provider: string;
    provider_id: string;
    title: string;
    subtitle?: string;
    search_author?: string;
    search_title?: string;
    authors?: string[];
    titles_by_language?: Record<string, string>;
    isbn_10?: string;
    isbn_13?: string;
    cover_url?: string;
    publish_year?: number;
    language?: string;
  };
  sources_searched: string[];
  errors?: string[];
  column_config?: ReleaseColumnConfig | null; // Plugin-driven column configuration
  search_info?: Record<string, SourceSearchInfo>; // Per-source search metadata
}

// Search status update from WebSocket (for ReleaseModal loading state)
export interface SearchStatusData {
  source: string; // Release source name (e.g., 'irc', 'direct_download')
  provider: string; // Metadata provider (may be empty)
  book_id: string; // Book ID (may be empty)
  message: string; // Human-readable status message
  phase: 'connecting' | 'searching' | 'downloading' | 'parsing' | 'complete' | 'error';
}

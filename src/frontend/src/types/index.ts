// Search mode constants and type
export const SEARCH_MODE = {
  DIRECT: 'direct',
  UNIVERSAL: 'universal',
} as const;

export type SearchMode = typeof SEARCH_MODE[keyof typeof SEARCH_MODE];

// Display field for metadata cards (provider-specific info like ratings, pages, etc.)
export interface DisplayField {
  label: string;      // e.g., "Rating", "Pages", "Readers"
  value: string;      // e.g., "4.5", "496", "8,041"
  icon?: string;      // Icon name: "star", "book", "users", "editions"
}

// Book data types
export interface Book {
  id: string;
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
  status_message?: string;  // Detailed status message (e.g., "Trying Libgen (2/5)")
  added_time?: number;  // Timestamp when added to queue
  source?: string;  // Release source handler (e.g., "direct_download", "prowlarr")
  source_display_name?: string;  // Human-readable source name (e.g., "Direct Download")
  // Metadata provider fields (used in universal search mode)
  provider?: string;           // e.g., 'hardcover', 'openlibrary'
  provider_display_name?: string;  // e.g., 'Hardcover', 'Open Library'
  provider_id?: string;        // ID in provider's system
  isbn_10?: string;
  isbn_13?: string;
  genres?: string[];
  source_url?: string;         // Link to book on provider's site
  display_fields?: DisplayField[];  // Provider-specific display data
  // Series info (if book is part of a series)
  series_name?: string;        // Name of the series
  series_position?: number;    // This book's position (e.g., 3, 1.5 for novellas)
  series_count?: number;       // Total books in the series
}

// Status response types
export interface StatusData {
  queued?: Record<string, Book>;
  resolving?: Record<string, Book>;
  downloading?: Record<string, Book>;
  complete?: Record<string, Book>;
  error?: Record<string, Book>;
  cancelled?: Record<string, Book>;
}

export interface ActiveDownloadsResponse {
  active_downloads: Book[];
}

// Button states
export type ButtonState = 'download' | 'queued' | 'resolving' | 'downloading' | 'complete' | 'error';

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
}

// Search field types (mirror backend search field types)
export type SearchFieldType =
  | 'TextSearchField'
  | 'NumberSearchField'
  | 'SelectSearchField'
  | 'CheckboxSearchField';

interface SearchFieldBase {
  key: string;
  label: string;
  type: SearchFieldType;
  placeholder?: string;
  description?: string;
}

export interface TextSearchField extends SearchFieldBase {
  type: 'TextSearchField';
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

export type MetadataSearchField =
  | TextSearchField
  | NumberSearchField
  | SelectSearchField
  | CheckboxSearchField;

// App configuration
// Content type for search (ebook vs audiobook)
export type ContentType = 'ebook' | 'audiobook';

export interface AppConfig {
  calibre_web_url: string;
  debug: boolean;
  build_version: string;
  release_version: string;
  book_languages: Language[];
  default_language: string[];
  supported_formats: string[];
  supported_audiobook_formats: string[];  // Audiobook formats (m4b, mp3)
  search_mode: SearchMode;
  metadata_sort_options: SortOption[];
  metadata_search_fields: MetadataSearchField[];
  default_release_source?: string;  // Default tab in ReleaseModal (e.g., 'direct_download')
  auto_open_downloads_sidebar: boolean;  // Auto-open sidebar when download is queued
  download_to_browser: boolean;  // Auto-download completed files to browser
  settings_enabled: boolean;  // Whether config directory is mounted and writable
  default_sort: string;  // Default sort for direct mode (Anna's Archive)
  metadata_default_sort: string;  // Default sort for universal mode (from metadata provider)
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
  error?: string;
}

// Type guard to check if a book is from a metadata provider
// Returns true and narrows type to include required provider fields
export const isMetadataBook = (book: Book): book is Book & {
  provider: string;
  provider_id: string;
} => {
  return !!book.provider && !!book.provider_id;
};

// Release source types (from plugin system)
export interface ReleaseSource {
  name: string;           // e.g., 'direct_download', 'prowlarr'
  display_name: string;   // e.g., 'Direct Download', 'Prowlarr'
  enabled: boolean;       // Whether the source is available for use
  supported_content_types?: string[];  // Content types this source supports (e.g., ['ebook', 'audiobook'])
}

// Column schema types for plugin-driven release list UI
export type ColumnRenderType = 'text' | 'badge' | 'size' | 'number' | 'peers';
export type ColumnAlign = 'left' | 'center' | 'right';

export interface ColumnColorHint {
  type: 'map' | 'static';  // 'map' uses colorMaps.ts, 'static' is a fixed Tailwind class
  value: string;           // Map name ('format', 'language') or Tailwind class
}

export interface ColumnSchema {
  key: string;             // Data path (e.g., 'format', 'extra.language')
  label: string;           // Accessibility label
  render_type: ColumnRenderType;
  align: ColumnAlign;
  width: string;           // CSS width (e.g., '80px')
  hide_mobile: boolean;    // Hide on small screens
  color_hint?: ColumnColorHint | null;
  fallback: string;        // Value when data is missing
  uppercase: boolean;      // Force uppercase display
}

// Leading cell config - what to show in the left-most position of each row
export type LeadingCellType = 'thumbnail' | 'badge' | 'none';

export interface LeadingCellConfig {
  type: LeadingCellType;
  key?: string;                      // Field path for data (e.g., 'extra.preview' or 'extra.download_type')
  color_hint?: ColumnColorHint;      // For badge type - maps values to colors
  uppercase?: boolean;               // Force uppercase for badge text
}

export interface ReleaseColumnConfig {
  columns: ColumnSchema[];
  grid_template: string;             // CSS grid-template-columns for dynamic section
  leading_cell?: LeadingCellConfig;  // Defaults to thumbnail from extra.preview
  online_servers?: string[];         // For IRC: list of currently online server nicks
  cache_ttl_seconds?: number;        // How long to cache results (default: 300 = 5 min)
  supported_filters?: string[];      // Which filters this source supports: ["format", "language"]
}

// A downloadable release from any source
export interface Release {
  source: string;              // Source plugin name
  source_id: string;           // ID within that source
  title: string;
  format?: string;             // epub, pdf, mobi, etc.
  language?: string;           // ISO 639-1 code (e.g., "en", "de", "fr")
  size?: string;               // Human-readable size
  size_bytes?: number;         // Size in bytes for sorting
  download_url?: string;
  info_url?: string;           // Link to release info page (e.g., tracker page) - makes title clickable
  protocol?: 'http' | 'torrent' | 'nzb' | 'dcc';
  indexer?: string;            // Display name for the source/indexer
  seeders?: number;            // For torrents
  peers?: string;              // For torrents: "seeders/leechers" display string
  extra?: Record<string, unknown>; // Source-specific metadata
}

// Search info returned by release sources
export interface SourceSearchInfo {
  search_type: 'isbn' | 'title_author' | 'categories' | 'expanded';
}

// Response from /api/releases endpoint
export interface ReleasesResponse {
  releases: Release[];
  book: {
    provider: string;
    provider_id: string;
    title: string;
    authors?: string[];
    isbn_10?: string;
    isbn_13?: string;
    cover_url?: string;
    publish_year?: number;
    language?: string;
  };
  sources_searched: string[];
  errors?: string[];
  column_config?: ReleaseColumnConfig | null;  // Plugin-driven column configuration
  search_info?: Record<string, SourceSearchInfo>;  // Per-source search metadata
}

// Search status update from WebSocket (for ReleaseModal loading state)
export interface SearchStatusData {
  source: string;      // Release source name (e.g., 'irc', 'direct_download')
  provider: string;    // Metadata provider (may be empty)
  book_id: string;     // Book ID (may be empty)
  message: string;     // Human-readable status message
  phase: 'connecting' | 'searching' | 'downloading' | 'parsing' | 'complete' | 'error';
}

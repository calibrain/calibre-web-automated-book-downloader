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

// App configuration
export interface AppConfig {
  calibre_web_url: string;
  debug: boolean;
  build_version: string;
  release_version: string;
  book_languages: Language[];
  default_language: string[];
  supported_formats: string[];
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

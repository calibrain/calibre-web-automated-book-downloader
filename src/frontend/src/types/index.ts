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
  download_path?: string;
  progress?: number;
}

// Status response types
export interface StatusData {
  queued?: Record<string, Book>;
  downloading?: Record<string, Book>;
  available?: Record<string, Book>;
  done?: Record<string, Book>;
  completed?: Record<string, Book>;
  error?: Record<string, Book>;
}

export interface ActiveDownloadsResponse {
  active_downloads: Book[];
}

// Button states
export type ButtonState = 'download' | 'queued' | 'downloading';

export interface ButtonStateInfo {
  text: string;
  state: ButtonState;
}

// Language option
export interface Language {
  code: string;
  language: string;
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
  app_env: string;
  build_version: string;
  release_version: string;
  book_languages: Language[];
  default_language: string;
  supported_formats: string[];
}

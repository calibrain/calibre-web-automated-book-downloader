// Color styles with transparent backgrounds and contrasting text (matches DownloadsSidebar style)
interface ColorStyle {
  bg: string;
  text: string;
}

const FORMAT_COLORS: Record<string, ColorStyle> = {
  pdf: { bg: 'bg-red-500/20', text: 'text-red-700 dark:text-red-300' },
  epub: { bg: 'bg-green-500/20', text: 'text-green-700 dark:text-green-300' },
  mobi: { bg: 'bg-blue-500/20', text: 'text-blue-700 dark:text-blue-300' },
  azw3: { bg: 'bg-purple-500/20', text: 'text-purple-700 dark:text-purple-300' },
  txt: { bg: 'bg-gray-500/20', text: 'text-gray-700 dark:text-gray-300' },
  djvu: { bg: 'bg-orange-500/20', text: 'text-orange-700 dark:text-orange-300' },
  fb2: { bg: 'bg-teal-500/20', text: 'text-teal-700 dark:text-teal-300' },
  cbr: { bg: 'bg-yellow-500/20', text: 'text-yellow-700 dark:text-yellow-300' },
  cbz: { bg: 'bg-amber-500/20', text: 'text-amber-700 dark:text-amber-300' },
};

const LANGUAGE_COLORS: Record<string, ColorStyle> = {
  en: { bg: 'bg-blue-500/20', text: 'text-blue-700 dark:text-blue-300' },
  english: { bg: 'bg-blue-500/20', text: 'text-blue-700 dark:text-blue-300' },
  es: { bg: 'bg-orange-500/20', text: 'text-orange-700 dark:text-orange-300' },
  spanish: { bg: 'bg-orange-500/20', text: 'text-orange-700 dark:text-orange-300' },
  fr: { bg: 'bg-purple-500/20', text: 'text-purple-700 dark:text-purple-300' },
  french: { bg: 'bg-purple-500/20', text: 'text-purple-700 dark:text-purple-300' },
  de: { bg: 'bg-yellow-500/20', text: 'text-yellow-700 dark:text-yellow-300' },
  german: { bg: 'bg-yellow-500/20', text: 'text-yellow-700 dark:text-yellow-300' },
  it: { bg: 'bg-green-500/20', text: 'text-green-700 dark:text-green-300' },
  italian: { bg: 'bg-green-500/20', text: 'text-green-700 dark:text-green-300' },
  pt: { bg: 'bg-teal-500/20', text: 'text-teal-700 dark:text-teal-300' },
  portuguese: { bg: 'bg-teal-500/20', text: 'text-teal-700 dark:text-teal-300' },
  ru: { bg: 'bg-red-500/20', text: 'text-red-700 dark:text-red-300' },
  russian: { bg: 'bg-red-500/20', text: 'text-red-700 dark:text-red-300' },
  ja: { bg: 'bg-pink-500/20', text: 'text-pink-700 dark:text-pink-300' },
  japanese: { bg: 'bg-pink-500/20', text: 'text-pink-700 dark:text-pink-300' },
  zh: { bg: 'bg-rose-500/20', text: 'text-rose-700 dark:text-rose-300' },
  chinese: { bg: 'bg-rose-500/20', text: 'text-rose-700 dark:text-rose-300' },
};

const DOWNLOAD_TYPE_COLORS: Record<string, ColorStyle> = {
  torrent: { bg: 'bg-orange-500/20', text: 'text-orange-700 dark:text-orange-300' },
  usenet: { bg: 'bg-sky-500/20', text: 'text-sky-700 dark:text-sky-300' },
  nzb: { bg: 'bg-sky-500/20', text: 'text-sky-700 dark:text-sky-300' },
  ddl: { bg: 'bg-emerald-500/20', text: 'text-emerald-700 dark:text-emerald-300' },
  direct: { bg: 'bg-emerald-500/20', text: 'text-emerald-700 dark:text-emerald-300' },
};

const DEFAULT_FORMAT_COLOR: ColorStyle = { bg: 'bg-cyan-500/20', text: 'text-cyan-700 dark:text-cyan-300' };
const DEFAULT_LANGUAGE_COLOR: ColorStyle = { bg: 'bg-indigo-500/20', text: 'text-indigo-700 dark:text-indigo-300' };
const DEFAULT_DOWNLOAD_TYPE_COLOR: ColorStyle = { bg: 'bg-violet-500/20', text: 'text-violet-700 dark:text-violet-300' };
const FALLBACK_COLOR: ColorStyle = { bg: 'bg-gray-500/20', text: 'text-gray-700 dark:text-gray-300' };

export function getFormatColor(format?: string): ColorStyle {
  if (!format || format === '-') return FALLBACK_COLOR;
  return FORMAT_COLORS[format.toLowerCase()] || DEFAULT_FORMAT_COLOR;
}

export function getLanguageColor(language?: string): ColorStyle {
  if (!language || language === '-') return FALLBACK_COLOR;
  return LANGUAGE_COLORS[language.toLowerCase()] || DEFAULT_LANGUAGE_COLOR;
}

export function getDownloadTypeColor(downloadType?: string): ColorStyle {
  if (!downloadType || downloadType === '-') return FALLBACK_COLOR;
  return DOWNLOAD_TYPE_COLORS[downloadType.toLowerCase()] || DEFAULT_DOWNLOAD_TYPE_COLOR;
}

export type { ColorStyle };

import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { Book, Release, ReleaseSource, ReleasesResponse, Language, StatusData, ButtonStateInfo, ColumnSchema, ReleaseColumnConfig, LeadingCellConfig, SearchStatusData, ContentType } from '../types';
import { getReleases, getReleaseSources } from '../services/api';
import { useSocket } from '../contexts/SocketContext';
import { Dropdown } from './Dropdown';
import { DropdownList } from './DropdownList';
import { BookDownloadButton } from './BookDownloadButton';
import { ReleaseCell } from './ReleaseCell';
import { getColorStyleFromHint } from '../utils/colorMaps';
import { getNestedValue } from '../utils/objectHelpers';
import { LanguageMultiSelect } from './LanguageMultiSelect';
import { LANGUAGE_OPTION_ALL, LANGUAGE_OPTION_DEFAULT, getLanguageFilterValues, releaseLanguageMatchesFilter } from '../utils/languageFilters';

// Module-level cache for release search results
// Key format: `${provider}:${provider_id}:${source}`
// This persists across modal open/close cycles
const releaseCache = new Map<string, ReleasesResponse>();

function getCacheKey(provider: string, providerId: string, source: string): string {
  return `${provider}:${providerId}:${source}`;
}

// Default cache TTL (5 minutes) - sources can override via column_config.cache_ttl_seconds
const DEFAULT_CACHE_TTL_MS = 5 * 60 * 1000;
const cacheTimestamps = new Map<string, number>();

function getCachedReleases(provider: string, providerId: string, source: string): ReleasesResponse | null {
  const key = getCacheKey(provider, providerId, source);
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

function setCachedReleases(provider: string, providerId: string, source: string, data: ReleasesResponse): void {
  const key = getCacheKey(provider, providerId, source);
  releaseCache.set(key, data);
  cacheTimestamps.set(key, Date.now());
}

// Default column configuration (fallback when backend doesn't provide one)
const DEFAULT_COLUMN_CONFIG: ReleaseColumnConfig = {
  columns: [
    {
      key: 'extra.language',
      label: 'Language',
      render_type: 'badge',
      align: 'center',
      width: '60px',
      hide_mobile: false,  // Language shown on mobile
      color_hint: { type: 'map', value: 'language' },
      fallback: '-',
      uppercase: true,
    },
    {
      key: 'format',
      label: 'Format',
      render_type: 'badge',
      align: 'center',
      width: '80px',
      hide_mobile: false,  // Format shown on mobile
      color_hint: { type: 'map', value: 'format' },
      fallback: '-',
      uppercase: true,
    },
    {
      key: 'size',
      label: 'Size',
      render_type: 'size',
      align: 'center',
      width: '80px',
      hide_mobile: false,  // Size shown on mobile
      fallback: '-',
      uppercase: false,
    },
  ],
  grid_template: 'minmax(0,2fr) 60px 80px 80px',
  supported_filters: ['format', 'language'],  // Default: both filters available
};

interface ReleaseModalProps {
  book: Book | null;
  onClose: () => void;
  onDownload: (book: Book, release: Release) => Promise<void>;
  supportedFormats: string[];
  supportedAudiobookFormats?: string[];  // Audiobook formats (m4b, mp3)
  contentType?: ContentType;  // 'ebook' or 'audiobook'
  defaultLanguages: string[];
  bookLanguages: Language[];
  currentStatus: StatusData;
  defaultReleaseSource?: string;  // Default tab to show (e.g., 'direct_download')
  onSearchSeries?: (seriesName: string) => void;  // Callback to search for series
}


// 5-star rating display with partial fill support
function StarRating({ rating, maxRating = 5 }: { rating: number; maxRating?: number }) {
  // Normalize rating to 0-5 scale if needed
  const normalizedRating = Math.min(Math.max(rating, 0), maxRating);

  return (
    <div className="flex items-center gap-0.5" title={`${rating} out of ${maxRating}`}>
      {[...Array(5)].map((_, index) => {
        const fillPercentage = Math.min(Math.max((normalizedRating - index) * 100, 0), 100);

        return (
          <div key={index} className="relative w-4 h-4">
            {/* Empty star (gray background) */}
            <svg
              className="absolute inset-0 w-4 h-4 text-gray-300 dark:text-gray-600"
              fill="currentColor"
              viewBox="0 0 20 20"
            >
              <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
            </svg>
            {/* Filled star (gold, clipped to fill percentage) */}
            <svg
              className="absolute inset-0 w-4 h-4 text-amber-400"
              fill="currentColor"
              viewBox="0 0 20 20"
              style={{ clipPath: `inset(0 ${100 - fillPercentage}% 0 0)` }}
            >
              <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
            </svg>
          </div>
        );
      })}
    </div>
  );
}

// Thumbnail component for release rows
const ReleaseThumbnail = ({ preview, title }: { preview?: string; title?: string }) => {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);

  if (!preview || imageError) {
    return (
      <div
        className="w-7 h-10 sm:w-8 sm:h-12 rounded bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-[7px] sm:text-[8px] font-medium text-gray-500 dark:text-gray-400 flex-shrink-0"
        aria-label="No cover available"
      >
        No Cover
      </div>
    );
  }

  return (
    <div className="relative w-7 h-10 sm:w-8 sm:h-12 rounded overflow-hidden bg-gray-100 dark:bg-gray-800 border border-white/40 dark:border-gray-700/70 flex-shrink-0">
      {!imageLoaded && (
        <div className="absolute inset-0 bg-gradient-to-r from-gray-200 via-gray-100 to-gray-200 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 animate-pulse" />
      )}
      <img
        src={preview}
        alt={title || 'Book cover'}
        className="w-full h-full object-cover object-top"
        loading="lazy"
        onLoad={() => setImageLoaded(true)}
        onError={() => setImageError(true)}
        style={{ opacity: imageLoaded ? 1 : 0, transition: 'opacity 0.2s ease-in-out' }}
      />
    </div>
  );
};

// Leading cell component - shows thumbnail, badge, or nothing based on config
const LeadingCell = ({
  config,
  release
}: {
  config?: LeadingCellConfig;
  release: Release;
}) => {
  // Default to thumbnail mode if no config
  const cellType = config?.type || 'thumbnail';

  if (cellType === 'none') {
    return null;
  }

  if (cellType === 'thumbnail') {
    const key = config?.key || 'extra.preview';
    const preview = getNestedValue(release as unknown as Record<string, unknown>, key) as string | undefined;
    return <ReleaseThumbnail preview={preview} title={release.title} />;
  }

  // Badge type
  if (cellType === 'badge' && config?.key) {
    const value = getNestedValue(release as unknown as Record<string, unknown>, config.key);
    const displayValue = value ? String(value) : '';
    const colorStyle = getColorStyleFromHint(displayValue, config.color_hint);
    const text = config.uppercase ? displayValue.toUpperCase() : displayValue;

    return (
      <div
        className={`w-7 h-10 sm:w-8 sm:h-12 rounded-lg ${colorStyle.bg} flex items-center justify-center flex-shrink-0`}
      >
        <span className={`text-[8px] sm:text-[9px] font-bold ${colorStyle.text} text-center leading-tight px-0.5`}>
          {text}
        </span>
      </div>
    );
  }

  // Fallback
  return <ReleaseThumbnail preview={undefined} title={release.title} />;
};

// Release row component with dynamic columns
const ReleaseRow = ({
  release,
  index,
  onDownload,
  buttonState,
  columns,
  gridTemplate,
  leadingCell,
  onlineServers,
}: {
  release: Release;
  index: number;
  onDownload: () => Promise<void>;
  buttonState: ButtonStateInfo;
  columns: ColumnSchema[];
  gridTemplate: string;
  leadingCell?: LeadingCellConfig;
  onlineServers?: string[];
}) => {
  const author = release.extra?.author as string | undefined;

  // Filter columns visible on mobile
  const mobileColumns = columns.filter((c) => !c.hide_mobile);

  // Determine if leading cell should be shown
  // Default to showing thumbnail if no config provided, hide only if explicitly set to 'none'
  const showLeadingCell = leadingCell?.type !== 'none';

  // Build grid template based on whether leading cell is shown
  const desktopGridTemplate = showLeadingCell
    ? `auto ${gridTemplate} auto`
    : `${gridTemplate} auto`;

  const mobileGridTemplate = showLeadingCell
    ? 'auto 1fr auto'
    : '1fr auto';

  return (
    <div
      className="pl-5 pr-2 sm:pr-5 py-2 transition-colors duration-200 hover-row animate-slide-up will-change-transform"
      style={{
        animationDelay: `${index * 30}ms`,
        animationFillMode: 'both',
      }}
    >
      {/* Desktop layout with dynamic grid */}
      <div
        className="hidden sm:grid items-center gap-3"
        style={{ gridTemplateColumns: desktopGridTemplate }}
      >
        {/* Leading cell: Thumbnail, Badge, or nothing */}
        {showLeadingCell && <LeadingCell config={leadingCell} release={release} />}

        {/* Fixed: Title and author */}
        <div className="min-w-0">
          <p className="text-sm font-medium line-clamp-2" title={release.title}>
            {release.info_url ? (
              <a
                href={release.info_url}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:text-emerald-600 dark:hover:text-emerald-400 hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                {release.title}
              </a>
            ) : (
              release.title
            )}
          </p>
          {author && (
            <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
              {author}
            </p>
          )}
        </div>

        {/* Dynamic columns from schema */}
        {columns.map((col) => (
          <ReleaseCell key={col.key} column={col} release={release} onlineServers={onlineServers} />
        ))}

        {/* Fixed: Action button */}
        <BookDownloadButton
          buttonState={buttonState}
          onDownload={onDownload}
          variant="icon"
          size="sm"
          ariaLabel={`Download ${release.title}`}
        />
      </div>

      {/* Mobile layout - author inline with title, info line below */}
      <div
        className="grid sm:hidden items-center gap-2"
        style={{ gridTemplateColumns: mobileGridTemplate }}
      >
        {/* Leading cell: Thumbnail, Badge, or nothing */}
        {showLeadingCell && <LeadingCell config={leadingCell} release={release} />}

        <div className="min-w-0">
          {/* Title and author on same line */}
          <p className="text-sm leading-tight line-clamp-2" title={release.title}>
            {release.info_url ? (
              <a
                href={release.info_url}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium hover:text-emerald-600 dark:hover:text-emerald-400 hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                {release.title}
              </a>
            ) : (
              <span className="font-medium">{release.title}</span>
            )}
            {author && (
              <span className="text-gray-500 dark:text-gray-400 font-normal"> — {author}</span>
            )}
          </p>
          {/* Plugin-provided info line (format, size, indexer, seeders, etc.) */}
          {mobileColumns.length > 0 && (
            <div className="flex items-center gap-1.5 mt-1 text-[10px] text-gray-500 dark:text-gray-400">
              {mobileColumns.map((col, idx) => (
                <span key={col.key} className="flex items-center gap-1.5">
                  {idx > 0 && <span className="text-gray-300 dark:text-gray-600">·</span>}
                  <ReleaseCell column={col} release={release} compact onlineServers={onlineServers} />
                </span>
              ))}
            </div>
          )}
        </div>

        <BookDownloadButton
          buttonState={buttonState}
          onDownload={onDownload}
          variant="icon"
          size="sm"
          ariaLabel={`Download ${release.title}`}
        />
      </div>
    </div>
  );
};

// Shimmer block with wave animation - same as DownloadsSidebar
function ShimmerBlock({ className }: { className: string }) {
  return (
    <div
      className={`rounded bg-gray-200 dark:bg-gray-800 relative overflow-hidden ${className}`}
    >
      <div
        className="absolute inset-0 dark:opacity-50"
        style={{
          background: 'linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.4) 50%, transparent 100%)',
          backgroundSize: '200% 100%',
          animation: 'wave 2s ease-in-out infinite',
        }}
      />
    </div>
  );
}

// Loading skeleton for releases - matches ReleaseRow layout
function ReleaseSkeleton() {
  return (
    <div className="divide-y divide-gray-200/60 dark:divide-gray-800/60">
      {[1, 2, 3, 4, 5].map((i) => (
        <div
          key={i}
          className="px-5 py-2"
          style={{
            opacity: 1 - (i - 1) * 0.15, // Fade out lower rows
          }}
        >
          <div className="grid grid-cols-[auto_1fr_auto] sm:grid-cols-[auto_minmax(0,2fr)_60px_80px_80px_auto] items-center gap-2 sm:gap-3">
            {/* Thumbnail skeleton */}
            <ShimmerBlock className="w-7 h-10 sm:w-10 sm:h-14" />

            {/* Title and author skeleton */}
            <div className="min-w-0 space-y-1.5">
              <ShimmerBlock className="h-4 w-3/4" />
              <ShimmerBlock className="h-3 w-1/2" />
            </div>

            {/* Language badge skeleton - desktop */}
            <div className="hidden sm:flex justify-center">
              <ShimmerBlock className="w-8 h-5" />
            </div>

            {/* Format badge skeleton - desktop */}
            <div className="hidden sm:flex justify-center">
              <ShimmerBlock className="w-12 h-5" />
            </div>

            {/* Size skeleton - desktop */}
            <div className="hidden sm:flex justify-center">
              <ShimmerBlock className="w-14 h-4" />
            </div>

            {/* Mobile info + action skeleton */}
            <div className="flex items-center gap-2">
              {/* Mobile: format + size inline */}
              <div className="flex sm:hidden flex-col items-end gap-1">
                <ShimmerBlock className="w-10 h-3" />
                <ShimmerBlock className="w-14 h-3" />
              </div>

              {/* Action button skeleton */}
              <ShimmerBlock className="w-8 h-8 !rounded-full" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

// Empty state component
function EmptyState({ message }: { message: string }) {
  return (
    <div className="text-center py-12 px-4">
      <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-gray-100 dark:bg-gray-800 mb-4">
        <svg
          className="w-7 h-7 text-gray-400"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"
          />
        </svg>
      </div>
      <p className="text-sm text-gray-500 dark:text-gray-400">{message}</p>
    </div>
  );
}

// Configure source CTA
function ConfigureSourceCTA({ sourceName }: { sourceName: string }) {
  return (
    <div className="text-center py-12 px-4">
      <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-amber-100 dark:bg-amber-900/30 mb-4">
        <svg
          className="w-7 h-7 text-amber-600 dark:text-amber-400"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.247a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z"
          />
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
        </svg>
      </div>
      <h4 className="text-base font-semibold text-gray-900 dark:text-gray-100 mb-2">
        {sourceName} Not Configured
      </h4>
      <p className="text-sm text-gray-500 dark:text-gray-400 max-w-xs mx-auto">
        Configure {sourceName} in Settings to enable searching this source.
      </p>
    </div>
  );
}


// Error state component
function ErrorState({ message }: { message: string }) {
  return (
    <div className="text-center py-12 px-4">
      <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-red-100 dark:bg-red-900/30 mb-4">
        <svg
          className="w-7 h-7 text-red-600 dark:text-red-400"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 9v3.75m9-.75a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9 3.75h.008v.008H12v-.008Z"
          />
        </svg>
      </div>
      <h4 className="text-base font-semibold text-gray-900 dark:text-gray-100 mb-2">
        Error Loading Releases
      </h4>
      <p className="text-sm text-gray-500 dark:text-gray-400 max-w-xs mx-auto">{message}</p>
    </div>
  );
}


export const ReleaseModal = ({
  book,
  onClose,
  onDownload,
  supportedFormats,
  supportedAudiobookFormats = [],
  contentType = 'ebook',
  defaultLanguages,
  bookLanguages,
  currentStatus,
  defaultReleaseSource,
  onSearchSeries,
}: ReleaseModalProps) => {
  // Use audiobook formats when in audiobook mode
  const effectiveFormats = contentType === 'audiobook' && supportedAudiobookFormats.length > 0
    ? supportedAudiobookFormats
    : supportedFormats;
  const [isClosing, setIsClosing] = useState(false);

  // Available sources from plugin registry
  const [availableSources, setAvailableSources] = useState<ReleaseSource[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(true);

  // Active tab (source name)
  const [activeTab, setActiveTab] = useState<string>('');

  // Track if book summary has scrolled out of view
  const [showHeaderThumb, setShowHeaderThumb] = useState(false);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const bookSummaryRef = useRef<HTMLDivElement>(null);

  // Releases data per source
  const [releasesBySource, setReleasesBySource] = useState<Record<string, ReleasesResponse | null>>({});
  const [loadingBySource, setLoadingBySource] = useState<Record<string, boolean>>({});
  const [errorBySource, setErrorBySource] = useState<Record<string, string | null>>({});
  const [expandedBySource, setExpandedBySource] = useState<Record<string, boolean>>({});

  // Search status from WebSocket (for showing progress during slow searches like IRC)
  const [searchStatus, setSearchStatus] = useState<SearchStatusData | null>(null);
  const { socket } = useSocket();
  const lastStatusTimeRef = useRef<number>(0);
  const pendingStatusRef = useRef<SearchStatusData | null>(null);
  const statusTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Filters - initialized from config settings
  // Empty string means "show all supported formats" (filtered by supportedFormats)
  // A specific value means "show only that format"
  const [formatFilter, setFormatFilter] = useState<string>('');
  const [languageFilter, setLanguageFilter] = useState<string[]>([LANGUAGE_OPTION_DEFAULT]);

  // Description expansion
  const [descriptionExpanded, setDescriptionExpanded] = useState(false);
  const [descriptionOverflows, setDescriptionOverflows] = useState(false);
  const descriptionRef = useRef<HTMLParagraphElement>(null);

  // Close handler with animation
  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
    }, 150);
  }, [onClose]);

  // Handle ESC key
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose();
    };
    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [handleClose]);

  // Body scroll lock
  useEffect(() => {
    if (book) {
      const previousOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = previousOverflow;
      };
    }
  }, [book]);

  // Reset modal state when book changes to prevent stale data
  useEffect(() => {
    setDescriptionExpanded(false);
    setDescriptionOverflows(false);
    setReleasesBySource({});
    setLoadingBySource({});
    setErrorBySource({});
    setExpandedBySource({});
    setFormatFilter('');
    setLanguageFilter([LANGUAGE_OPTION_DEFAULT]);
    setSearchStatus(null);
    lastStatusTimeRef.current = 0;
    pendingStatusRef.current = null;
    if (statusTimeoutRef.current) {
      clearTimeout(statusTimeoutRef.current);
      statusTimeoutRef.current = null;
    }
  }, [book?.id]);

  // Set up WebSocket listener for search status updates
  useEffect(() => {
    if (!book || !socket) return;

    const MIN_DISPLAY_TIME = 1500; // Minimum ms to show each status message

    const handleSearchStatus = (data: SearchStatusData) => {
      // Only handle status for the current active tab
      if (data.source !== activeTab) return;

      const now = Date.now();
      const elapsed = now - lastStatusTimeRef.current;

      // If enough time has passed, update immediately
      if (elapsed >= MIN_DISPLAY_TIME) {
        setSearchStatus(data);
        lastStatusTimeRef.current = now;
        pendingStatusRef.current = null;
      } else {
        // Queue the update for later
        pendingStatusRef.current = data;

        // Clear any existing timeout
        if (statusTimeoutRef.current) {
          clearTimeout(statusTimeoutRef.current);
        }

        // Schedule update after remaining time
        statusTimeoutRef.current = setTimeout(() => {
          if (pendingStatusRef.current) {
            setSearchStatus(pendingStatusRef.current);
            lastStatusTimeRef.current = Date.now();
            pendingStatusRef.current = null;
          }
        }, MIN_DISPLAY_TIME - elapsed);
      }
    };

    socket.on('search_status', handleSearchStatus);

    return () => {
      socket.off('search_status', handleSearchStatus);
      if (statusTimeoutRef.current) {
        clearTimeout(statusTimeoutRef.current);
      }
    };
  }, [book, socket, activeTab]);

  // Clear search status when loading finishes
  useEffect(() => {
    if (!loadingBySource[activeTab]) {
      setSearchStatus(null);
    }
  }, [loadingBySource, activeTab]);

  // Check if description text overflows (needs "more" button)
  useEffect(() => {
    const el = descriptionRef.current;
    if (el && !descriptionExpanded) {
      // Compare scrollHeight to clientHeight to detect overflow
      setDescriptionOverflows(el.scrollHeight > el.clientHeight);
    }
  }, [book?.description, descriptionExpanded]);

  // Tab indicator refs and state for sliding animation
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const [tabIndicatorStyle, setTabIndicatorStyle] = useState({ left: 0, width: 0 });

  // Track scroll to show/hide header thumbnail
  useEffect(() => {
    const scrollContainer = scrollContainerRef.current;
    const bookSummary = bookSummaryRef.current;
    if (!scrollContainer || !bookSummary) return;

    const handleScroll = () => {
      const summaryRect = bookSummary.getBoundingClientRect();
      const containerRect = scrollContainer.getBoundingClientRect();
      // Show header thumb when the book summary section has scrolled past the top
      setShowHeaderThumb(summaryRect.bottom < containerRect.top + 20);
    };

    scrollContainer.addEventListener('scroll', handleScroll, { passive: true });
    return () => scrollContainer.removeEventListener('scroll', handleScroll);
  }, [book]);

  // Fetch available sources on mount
  useEffect(() => {
    if (!book) return;

    const fetchSources = async () => {
      try {
        setSourcesLoading(true);
        const sources = await getReleaseSources();
        setAvailableSources(sources);

        // Filter sources by content type support
        const supportedSources = sources.filter(s => {
          const types = s.supported_content_types || ['ebook', 'audiobook'];
          return types.includes(contentType);
        });

        // Set active tab: prefer defaultReleaseSource if enabled and supports content type
        if (supportedSources.length > 0) {
          const enabledSources = supportedSources.filter(s => s.enabled);
          const defaultIsEnabled = defaultReleaseSource &&
            enabledSources.some(s => s.name === defaultReleaseSource);

          let defaultSource: string;
          if (defaultIsEnabled) {
            defaultSource = defaultReleaseSource;
          } else if (enabledSources.length > 0) {
            defaultSource = enabledSources[0].name;
          } else {
            defaultSource = supportedSources[0].name;  // Fallback to first supported source
          }
          setActiveTab(defaultSource);
        } else if (sources.length > 0) {
          // No sources support this content type - fall back to first source
          setActiveTab(sources[0].name);
        }
      } catch (err) {
        console.error('Failed to fetch release sources:', err);
        // Fallback: assume direct_download is available (for ebooks)
        setAvailableSources([{
          name: 'direct_download',
          display_name: "Anna's Archive",
          enabled: true,
          supported_content_types: ['ebook']
        }]);
        if (contentType === 'ebook') {
          setActiveTab('direct_download');
        }
      } finally {
        setSourcesLoading(false);
      }
    };

    fetchSources();
  }, [book, defaultReleaseSource, contentType]);

  // Fetch releases when active tab changes (with caching)
  // Initial fetch always uses ISBN-first search; expansion is handled by handleExpandSearch
  useEffect(() => {
    if (!book || !activeTab || !book.provider || !book.provider_id) return;

    // Extract to local variables for TypeScript narrowing
    const provider = book.provider;
    const bookId = book.provider_id;

    // Skip if already loaded, currently loading, or has error (prevents retry loop)
    if (releasesBySource[activeTab] !== undefined || loadingBySource[activeTab] || errorBySource[activeTab]) return;

    // Check module-level cache first
    const cached = getCachedReleases(provider, bookId, activeTab);
    if (cached) {
      setReleasesBySource((prev) => ({ ...prev, [activeTab]: cached }));
      return;
    }

    const fetchReleases = async () => {
      setLoadingBySource((prev) => ({ ...prev, [activeTab]: true }));
      setErrorBySource((prev) => ({ ...prev, [activeTab]: null }));

      try {
        const response = await getReleases(provider, bookId, activeTab, book.title, book.author, undefined, undefined, contentType);
        setCachedReleases(provider, bookId, activeTab, response);
        setReleasesBySource((prev) => ({ ...prev, [activeTab]: response }));
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to fetch releases';
        setErrorBySource((prev) => ({ ...prev, [activeTab]: message }));
      } finally {
        setLoadingBySource((prev) => ({ ...prev, [activeTab]: false }));
      }
    };

    fetchReleases();
  }, [book, activeTab, releasesBySource, loadingBySource, errorBySource, contentType]);

  // Handler for expanding search (title+author instead of ISBN)
  // Fetches additional results and merges with existing ISBN results
  const handleExpandSearch = useCallback(async () => {
    if (!activeTab || !book?.provider || !book?.provider_id) return;

    const provider = book.provider;
    const bookId = book.provider_id;

    // Mark as loading and expanded
    setLoadingBySource((prev) => ({ ...prev, [activeTab]: true }));
    setExpandedBySource((prev) => ({ ...prev, [activeTab]: true }));

    try {
      // Resolve language codes for the API call (same logic as Apply button)
      const langCodes = getLanguageFilterValues(languageFilter, bookLanguages, defaultLanguages);
      const languagesParam = (langCodes === null || langCodes?.includes(LANGUAGE_OPTION_ALL))
        ? undefined
        : langCodes;

      // Fetch with expand_search=true (title+author search)
      const expandedResponse = await getReleases(
        provider, bookId, activeTab, book.title, book.author, true, languagesParam, contentType
      );

      // Merge with existing results, deduplicating by source_id
      setReleasesBySource((prev) => {
        const existing = prev[activeTab];
        if (!existing) {
          return { ...prev, [activeTab]: expandedResponse };
        }

        const seenIds = new Set(existing.releases.map(r => r.source_id));
        const newReleases = expandedResponse.releases.filter(r => !seenIds.has(r.source_id));

        return {
          ...prev,
          [activeTab]: {
            ...existing,
            releases: [...existing.releases, ...newReleases],
          },
        };
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to expand search';
      setErrorBySource((prev) => ({ ...prev, [activeTab]: message }));
    } finally {
      setLoadingBySource((prev) => ({ ...prev, [activeTab]: false }));
    }
  }, [activeTab, book, languageFilter, bookLanguages, defaultLanguages, contentType]);

  // Build list of tabs to show
  // All sources come from backend with their enabled status
  // Filter by supported content types, then order: 1) Default source, 2) Other enabled sources, 3) Disabled sources
  const allTabs = useMemo(() => {
    type TabInfo = { name: string; displayName: string; enabled: boolean };

    const enabledTabs: TabInfo[] = [];
    const disabledTabs: TabInfo[] = [];

    // Filter sources by content type and separate by enabled status
    availableSources.forEach((src) => {
      // Check if source supports the current content type
      const supportedTypes = src.supported_content_types || ['ebook', 'audiobook'];
      if (!supportedTypes.includes(contentType)) {
        return; // Skip sources that don't support this content type
      }

      const tab = { name: src.name, displayName: src.display_name, enabled: src.enabled };
      if (src.enabled) {
        enabledTabs.push(tab);
      } else {
        disabledTabs.push(tab);
      }
    });

    // Sort enabled tabs so default source appears first
    if (defaultReleaseSource) {
      enabledTabs.sort((a, b) => {
        if (a.name === defaultReleaseSource) return -1;
        if (b.name === defaultReleaseSource) return 1;
        return 0;
      });
    }

    // Combine: enabled sources first (with default first), then disabled
    return [...enabledTabs, ...disabledTabs];
  }, [availableSources, defaultReleaseSource, contentType]);

  // Update tab indicator position when active tab changes
  useEffect(() => {
    const activeButton = tabRefs.current[activeTab];
    if (activeButton) {
      // Get position relative to parent container
      const containerRect = activeButton.parentElement?.getBoundingClientRect();
      const buttonRect = activeButton.getBoundingClientRect();
      if (containerRect) {
        setTabIndicatorStyle({
          left: buttonRect.left - containerRect.left,
          width: buttonRect.width,
        });
      }
    }
  }, [activeTab, allTabs]);

  // Get unique formats from current releases for filter dropdown
  // Only show formats that are in the supported list
  const availableFormats = useMemo(() => {
    const releases = releasesBySource[activeTab]?.releases || [];
    const formats = new Set<string>();
    const effectiveLower = effectiveFormats.map((f) => f.toLowerCase());

    releases.forEach((r) => {
      if (r.format) {
        const fmt = r.format.toLowerCase();
        // Only include formats that are in the supported list
        if (effectiveLower.includes(fmt)) {
          formats.add(fmt);
        }
      }
    });
    return Array.from(formats).sort();
  }, [releasesBySource, activeTab, effectiveFormats]);

  // Build select options for format filter
  const formatOptions = useMemo(() => {
    const options = [{ value: '', label: 'All Formats' }];
    availableFormats.forEach((fmt) => {
      options.push({ value: fmt, label: fmt.toUpperCase() });
    });
    return options;
  }, [availableFormats]);

  // Resolve language filter to actual language codes for filtering
  const resolvedLanguageCodes = useMemo(() => {
    return getLanguageFilterValues(languageFilter, bookLanguages, defaultLanguages);
  }, [languageFilter, bookLanguages, defaultLanguages]);

  // Filter releases based on settings and user selection
  const filteredReleases = useMemo(() => {
    const releases = releasesBySource[activeTab]?.releases || [];
    const effectiveLower = effectiveFormats.map((f) => f.toLowerCase());

    return releases.filter((r) => {
      // Format filtering
      const fmt = r.format?.toLowerCase();

      if (formatFilter) {
        // User selected a specific format - must match exactly
        if (!fmt || fmt !== formatFilter.toLowerCase()) return false;
      } else if (fmt) {
        // No specific filter - show only supported formats
        if (!effectiveLower.includes(fmt)) return false;
      }
      // Releases with no format pass through when no filter is set (show all)

      // Language filtering
      const releaseLang = r.extra?.language as string | undefined;
      if (!releaseLanguageMatchesFilter(releaseLang, resolvedLanguageCodes ?? defaultLanguages)) {
        return false;
      }

      return true;
    });
  }, [releasesBySource, activeTab, formatFilter, resolvedLanguageCodes, effectiveFormats, defaultLanguages]);

  // Get column config from response or use default
  const columnConfig = useMemo((): ReleaseColumnConfig => {
    const response = releasesBySource[activeTab];
    if (response?.column_config) {
      return response.column_config;
    }
    return DEFAULT_COLUMN_CONFIG;
  }, [releasesBySource, activeTab]);

  // Pre-compute display field lookups to avoid repeated .find() calls in JSX
  const displayFields = useMemo(() => {
    if (!book?.display_fields) return null;

    const starField = book.display_fields.find(f => f.icon === 'star');
    const ratingsField = book.display_fields.find(f => f.icon === 'ratings');
    const usersField = book.display_fields.find(f => f.icon === 'users');
    const pagesField = book.display_fields.find(f => f.icon === 'book');

    return { starField, ratingsField, usersField, pagesField };
  }, [book?.display_fields]);

  // Get button state for a release based on its source_id
  const getButtonState = useCallback(
    (releaseId: string): ButtonStateInfo => {
      // Check error first
      if (currentStatus.error && currentStatus.error[releaseId]) {
        return { text: 'Failed', state: 'error' };
      }
      // Check completed
      if (currentStatus.complete && currentStatus.complete[releaseId]) {
        return { text: 'Downloaded', state: 'complete' };
      }
      // Check in-progress states
      if (currentStatus.downloading && currentStatus.downloading[releaseId]) {
        const book = currentStatus.downloading[releaseId];
        return {
          text: 'Downloading',
          state: 'downloading',
          progress: book.progress,
        };
      }
      if (currentStatus.resolving && currentStatus.resolving[releaseId]) {
        return { text: 'Resolving', state: 'resolving' };
      }
      if (currentStatus.queued && currentStatus.queued[releaseId]) {
        return { text: 'Queued', state: 'queued' };
      }
      return { text: 'Download', state: 'download' };
    },
    [currentStatus]
  );

  // Handle download - close modal once download is successfully queued
  const handleDownload = useCallback(
    async (release: Release): Promise<void> => {
      if (book) {
        try {
          await onDownload(book, release);
          // Close modal after successful queue
          handleClose();
        } catch {
          // Keep modal open on error so user can try again
        }
      }
    },
    [book, onDownload, handleClose]
  );

  if (!book && !isClosing) return null;
  if (!book) return null;

  const titleId = `release-modal-title-${book.id}`;
  const providerDisplay =
    book.provider_display_name ||
    (book.provider ? book.provider.charAt(0).toUpperCase() + book.provider.slice(1) : 'Unknown');

  const currentTabLoading = loadingBySource[activeTab] ?? false;
  const currentTabError = errorBySource[activeTab] ?? null;
  const currentTabEnabled = allTabs.find((t) => t.name === activeTab)?.enabled ?? false;
  const isInitialLoading = currentTabLoading || (releasesBySource[activeTab] === undefined && !currentTabError);

  return (
    <div
      className="modal-overlay active sm:px-6 sm:py-6"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div
        className={`details-container w-full max-w-3xl h-full sm:h-auto ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex h-full sm:h-[90vh] sm:max-h-[90vh] flex-col overflow-hidden rounded-none sm:rounded-2xl border-0 sm:border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] text-[var(--text)] shadow-none sm:shadow-2xl">
          {/* Header */}
          <header className="flex items-start gap-3 border-b border-[var(--border-muted)] px-5 py-4">
            {/* Animated thumbnail that appears when scrolling */}
            <div
              className="flex-shrink-0 overflow-hidden transition-[width,margin] duration-300 ease-out"
              style={{
                width: showHeaderThumb ? 46 : 0,
                marginRight: showHeaderThumb ? 0 : -12,
              }}
            >
              <div
                className="transition-opacity duration-300 ease-out"
                style={{ opacity: showHeaderThumb ? 1 : 0 }}
              >
                {book.preview ? (
                  <img
                    src={book.preview}
                    alt=""
                    width={46}
                    height={68}
                    className="rounded shadow-md object-cover object-top"
                    style={{ width: 46, height: 68, minWidth: 46 }}
                  />
                ) : (
                  <div
                    className="rounded border border-dashed border-[var(--border-muted)] bg-[var(--bg)]/60 flex items-center justify-center text-[7px] text-gray-500"
                    style={{ width: 46, height: 68, minWidth: 46 }}
                  >
                    No cover
                  </div>
                )}
              </div>
            </div>
            <div className="flex-1 space-y-1 min-w-0">
              <p className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
                Find Releases
              </p>
              <h3 id={titleId} className="text-lg font-semibold leading-snug truncate">
                {book.title || 'Untitled'}
              </h3>
              <p className="text-sm text-gray-600 dark:text-gray-300 truncate">
                {book.author || 'Unknown author'}
              </p>
            </div>
            <button
              type="button"
              onClick={handleClose}
              className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100 flex-shrink-0"
              aria-label="Close"
            >
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </header>

          {/* Scrollable content */}
          <div ref={scrollContainerRef} className="flex-1 min-h-0 overflow-y-auto">
            {/* Book summary - scrolls with content */}
            <div ref={bookSummaryRef} className="flex gap-4 px-5 py-4 border-b border-[var(--border-muted)]">
              {book.preview ? (
                <img
                  src={book.preview}
                  alt="Book cover"
                  className={`rounded-lg shadow-md object-cover object-top flex-shrink-0 ${book.series_name ? 'w-24 h-[144px]' : 'w-20 h-[120px]'}`}
                />
              ) : (
                <div className={`rounded-lg border border-dashed border-[var(--border-muted)] bg-[var(--bg)]/60 flex items-center justify-center text-[10px] text-gray-500 flex-shrink-0 ${book.series_name ? 'w-24 h-[144px]' : 'w-20 h-[120px]'}`}>
                  No cover
                </div>
              )}
              <div className="flex-1 min-w-0 space-y-2">
                {/* Metadata row */}
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-gray-600 dark:text-gray-400">
                  {book.year && <span>{book.year}</span>}
                  {displayFields?.starField && (
                    <span className="flex items-center gap-1.5">
                      <StarRating rating={parseFloat(displayFields.starField.value || '0')} />
                      <span>{displayFields.starField.value}</span>
                      {displayFields.ratingsField && (
                        <span className="text-gray-400 dark:text-gray-500">({displayFields.ratingsField.value})</span>
                      )}
                    </span>
                  )}
                  {displayFields?.usersField && (
                    <span className="flex items-center gap-1">
                      <svg className="h-3.5 w-3.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z" />
                      </svg>
                      {displayFields.usersField.value} readers
                    </span>
                  )}
                  {displayFields?.pagesField && (
                    <span>{displayFields.pagesField.value} pages</span>
                  )}
                </div>

                {/* Series info */}
                {book.series_name && (
                  <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
                    <span>
                      {book.series_position != null ? (
                        <>#{Number.isInteger(book.series_position) ? book.series_position : book.series_position}{book.series_count ? ` of ${book.series_count}` : ''} in {book.series_name}</>
                      ) : (
                        <>Part of {book.series_name}</>
                      )}
                    </span>
                    {onSearchSeries && (
                      <button
                        type="button"
                        onClick={() => {
                          onSearchSeries(book.series_name!);
                          handleClose();
                        }}
                        className="inline-flex items-center gap-1 px-2 py-1 text-xs font-medium text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 rounded-full hover:bg-emerald-100 dark:hover:bg-emerald-900/40 transition-colors"
                      >
                        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
                        </svg>
                        View series
                      </button>
                    )}
                  </div>
                )}

                {/* Description */}
                {book.description && (
                  <div className="text-sm text-gray-600 dark:text-gray-400 relative">
                    <p ref={descriptionRef} className={descriptionExpanded ? '' : 'line-clamp-3'}>
                      {book.description}
                      {descriptionExpanded && descriptionOverflows && (
                        <>
                          {' '}
                          <button
                            type="button"
                            onClick={() => setDescriptionExpanded(false)}
                            className="text-emerald-600 dark:text-emerald-400 hover:underline font-medium inline"
                          >
                            Show less
                          </button>
                        </>
                      )}
                    </p>
                    {!descriptionExpanded && descriptionOverflows && (
                      <button
                        type="button"
                        onClick={() => setDescriptionExpanded(true)}
                        className="absolute bottom-0 right-0 text-emerald-600 dark:text-emerald-400 hover:underline font-medium pl-8 bg-gradient-to-r from-transparent via-[var(--bg)] to-[var(--bg)] sm:via-[var(--bg-soft)] sm:to-[var(--bg-soft)]"
                      >
                        more
                      </button>
                    )}
                  </div>
                )}

                {/* Links row */}
                <div className="flex flex-wrap items-center gap-3 text-xs">
                  {(book.isbn_13 || book.isbn_10) && (
                    <span className="text-gray-500 dark:text-gray-400">
                      ISBN: {book.isbn_13 || book.isbn_10}
                    </span>
                  )}
                  {book.source_url && (
                    <a
                      href={book.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400 hover:underline"
                    >
                      View on {providerDisplay}
                      <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                      </svg>
                    </a>
                  )}
                </div>
              </div>
            </div>

            {/* Source tabs + filters - sticky within scroll container */}
            <div className="sticky top-0 z-10 border-b border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)]">
              {sourcesLoading ? (
                <div className="flex gap-1 px-5 py-2">
                  <div className="h-10 w-32 animate-pulse bg-gray-200 dark:bg-gray-700 rounded" />
                </div>
              ) : (
                <div className="flex items-center justify-between px-5">
                  {/* Tabs - scrollable on narrow screens */}
                  <div className="flex-1 min-w-0 overflow-x-auto scrollbar-hide">
                    <div className="relative flex gap-1">
                      {/* Sliding indicator */}
                      <div
                        className="absolute bottom-0 h-0.5 bg-emerald-600 transition-all duration-300 ease-out"
                        style={{
                          left: tabIndicatorStyle.left,
                          width: tabIndicatorStyle.width,
                        }}
                      />
                      {allTabs.map((tab) => (
                          <button
                            key={tab.name}
                            ref={(el) => { tabRefs.current[tab.name] = el; }}
                            onClick={() => setActiveTab(tab.name)}
                            className={`px-4 py-2.5 text-sm font-medium border-b-2 border-transparent transition-colors whitespace-nowrap ${
                              activeTab === tab.name
                                ? 'text-emerald-600 dark:text-emerald-400'
                                : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
                            }`}
                          >
                            {tab.displayName}
                          </button>
                      ))}
                    </div>
                  </div>

                  {/* Filter funnel button - stays fixed */}
                  {/* Only show filter button if source supports at least one filter type */}
                  {((columnConfig.supported_filters?.includes('format') && availableFormats.length > 0) ||
                    (columnConfig.supported_filters?.includes('language') && bookLanguages.length > 0)) && (
                    <Dropdown
                      align="right"
                      widthClassName="w-auto flex-shrink-0"
                      panelClassName="w-56"
                      noScrollLimit
                      renderTrigger={({ isOpen, toggle }) => {
                        // Active filter: format is set, or language is not just default
                        const hasLanguageFilter = !(languageFilter.length === 1 && languageFilter[0] === LANGUAGE_OPTION_DEFAULT);
                        const hasActiveFilter = formatFilter !== '' || hasLanguageFilter;
                        return (
                          <button
                            type="button"
                            onClick={toggle}
                            className={`relative p-2 rounded-full transition-colors ${
                              isOpen
                                ? 'bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100'
                                : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800'
                            }`}
                            aria-label="Filter releases"
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 0 1-.659 1.591l-5.432 5.432a2.25 2.25 0 0 0-.659 1.591v2.927a2.25 2.25 0 0 1-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 0 0-.659-1.591L3.659 7.409A2.25 2.25 0 0 1 3 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0 1 12 3Z" />
                            </svg>
                            {hasActiveFilter && (
                              <span className="absolute top-1 right-1 w-2 h-2 bg-emerald-500 rounded-full" />
                            )}
                          </button>
                        );
                      }}
                    >
                      {({ close }) => (
                        <div className="p-4 space-y-4">
                          {columnConfig.supported_filters?.includes('format') && availableFormats.length > 0 && (
                            <DropdownList
                              label="Format"
                              options={formatOptions}
                              value={formatFilter}
                              onChange={(val) => setFormatFilter(typeof val === 'string' ? val : val[0] ?? '')}
                              placeholder="All Formats"
                            />
                          )}
                          {columnConfig.supported_filters?.includes('language') && (
                            <LanguageMultiSelect
                              label="Language"
                              options={bookLanguages}
                              value={languageFilter}
                              onChange={setLanguageFilter}
                              defaultLanguageCodes={defaultLanguages}
                            />
                          )}
                          {/* Apply button - for AA, re-fetches with language filter; for others, just closes */}
                          {activeTab === 'direct_download' && (
                            <button
                              type="button"
                              onClick={async () => {
                                close();
                                if (!book?.provider || !book?.provider_id) return;

                                const provider = book.provider;
                                const bookId = book.provider_id;

                                // Clear cache and state
                                const key = getCacheKey(provider, bookId, activeTab);
                                releaseCache.delete(key);
                                cacheTimestamps.delete(key);
                                setExpandedBySource((prev) => {
                                  const next = { ...prev };
                                  delete next[activeTab];
                                  return next;
                                });
                                setErrorBySource((prev) => {
                                  const next = { ...prev };
                                  delete next[activeTab];
                                  return next;
                                });

                                // Fetch with language filter
                                setLoadingBySource((prev) => ({ ...prev, [activeTab]: true }));
                                try {
                                  // Resolve language codes for the API call
                                  const langCodes = getLanguageFilterValues(languageFilter, bookLanguages, defaultLanguages);
                                  // Don't pass languages if "All" is selected or null
                                  const languagesParam = (langCodes === null || langCodes?.includes(LANGUAGE_OPTION_ALL))
                                    ? undefined
                                    : langCodes;

                                  const response = await getReleases(
                                    provider, bookId, activeTab, book.title, book.author, false, languagesParam, contentType
                                  );
                                  setCachedReleases(provider, bookId, activeTab, response);
                                  setReleasesBySource((prev) => ({ ...prev, [activeTab]: response }));
                                } catch (err) {
                                  const message = err instanceof Error ? err.message : 'Failed to fetch releases';
                                  setErrorBySource((prev) => ({ ...prev, [activeTab]: message }));
                                } finally {
                                  setLoadingBySource((prev) => ({ ...prev, [activeTab]: false }));
                                }
                              }}
                              className="w-full px-3 py-2 text-sm font-medium text-white bg-emerald-600 hover:bg-emerald-700 rounded-lg transition-colors"
                            >
                              Apply
                            </button>
                          )}
                        </div>
                      )}
                    </Dropdown>
                  )}
                </div>
              )}
            </div>

            {/* Release list content */}
            <div className="min-h-[200px]">
              {sourcesLoading ? (
                <ReleaseSkeleton />
              ) : !currentTabEnabled ? (
                <ConfigureSourceCTA
                  sourceName={allTabs.find((t) => t.name === activeTab)?.displayName || activeTab}
                />
              ) : isInitialLoading && filteredReleases.length === 0 ? (
                <ReleaseSkeleton />
              ) : currentTabError ? (
                <ErrorState message={currentTabError} />
              ) : filteredReleases.length === 0 && !currentTabLoading ? (
                <>
                  <EmptyState
                    message={
                      formatFilter
                        ? `No ${formatFilter.toUpperCase()} releases found. Try a different format.`
                        : 'No releases found for this book.'
                    }
                  />
                  {/* Expand search button - also shown in empty state */}
                  {!expandedBySource[activeTab] &&
                   releasesBySource[activeTab]?.search_info?.[activeTab]?.search_type &&
                   !['title_author', 'expanded'].includes(
                     releasesBySource[activeTab]?.search_info?.[activeTab]?.search_type ?? ''
                   ) && (
                    <div className="py-3 text-center">
                      <button
                        type="button"
                        onClick={handleExpandSearch}
                        className="px-3 py-1.5 text-sm text-gray-500 dark:text-gray-400 rounded-full hover-action transition-all duration-200"
                      >
                        Expand search
                      </button>
                    </div>
                  )}
                </>
              ) : (
                <>
                  {/* Key includes filter to force remount when filter changes */}
                  <div key={`releases-${formatFilter}-${languageFilter.join(',')}`} className="divide-y divide-gray-200/60 dark:divide-gray-800/60">
                    {filteredReleases.map((release, index) => (
                      <ReleaseRow
                        key={`${release.source}-${release.source_id}`}
                        release={release}
                        index={index}
                        onDownload={() => handleDownload(release)}
                        buttonState={getButtonState(release.source_id)}
                        columns={columnConfig.columns}
                        gridTemplate={columnConfig.grid_template}
                        leadingCell={columnConfig.leading_cell}
                        onlineServers={columnConfig.online_servers}
                      />
                    ))}
                  </div>
                  {/* Expand search button */}
                  {!expandedBySource[activeTab] &&
                   !currentTabLoading &&
                   releasesBySource[activeTab]?.search_info?.[activeTab]?.search_type &&
                   !['title_author', 'expanded'].includes(
                     releasesBySource[activeTab]?.search_info?.[activeTab]?.search_type ?? ''
                   ) && (
                    <div
                      className="py-3 text-center animate-slide-up will-change-transform"
                      style={{
                        animationDelay: `${filteredReleases.length * 30}ms`,
                        animationFillMode: 'both',
                      }}
                    >
                      <button
                        type="button"
                        onClick={handleExpandSearch}
                        className="px-3 py-1.5 text-sm text-gray-500 dark:text-gray-400 rounded-full hover-action transition-all duration-200"
                      >
                        Expand search
                      </button>
                    </div>
                  )}
                  {/* Expanding search - show skeleton below existing results */}
                  {currentTabLoading && filteredReleases.length > 0 && (
                    <ReleaseSkeleton />
                  )}
                </>
              )}
            </div>

            {/* Sticky search status indicator - stays at bottom of visible scroll area */}
            {searchStatus && searchStatus.source === activeTab && currentTabLoading && (
              <div className="sticky bottom-0 z-10 flex items-center justify-center pointer-events-none pb-4 pt-2">
                <div className="flex items-center gap-2.5 px-4 py-2 rounded-xl bg-[var(--bg-soft)] border border-[var(--border-muted)] text-gray-500 dark:text-gray-400 text-sm shadow-lg pointer-events-auto">
                  {searchStatus.phase !== 'complete' && searchStatus.phase !== 'error' && (
                    <div className="w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin" />
                  )}
                  {searchStatus.message}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

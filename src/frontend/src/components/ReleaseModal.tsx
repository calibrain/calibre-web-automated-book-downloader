import { useState, useCallback, useMemo, useRef, type KeyboardEventHandler } from 'react';
import { createPortal } from 'react-dom';

import { useDescriptionOverflow } from '../hooks/releaseModal/useDescriptionOverflow';
import { useHeaderThumbOnScroll } from '../hooks/releaseModal/useHeaderThumbOnScroll';
import { useReleaseSearchSession } from '../hooks/releaseModal/useReleaseSearchSession';
import { useTabIndicator } from '../hooks/ui/useTabIndicator';
import { useBodyScrollLock } from '../hooks/useBodyScrollLock';
import { useEscapeKey } from '../hooks/useEscapeKey';
import { inspectRelease } from '../services/api';
import type {
  Book,
  Release,
  Language,
  StatusData,
  ButtonStateInfo,
  ColumnSchema,
  ReleaseColumnConfig,
  LeadingCellConfig,
  ContentType,
  RequestPolicyMode,
  PackBook,
  PackPlan,
} from '../types';
import { isMetadataBook } from '../types';
import { bookSupportsTargets } from '../utils/bookTargetLoader';
import { getColorStyleFromHint } from '../utils/colorMaps';
import {
  LANGUAGE_OPTION_DEFAULT,
  getLanguageFilterValues,
  releaseLanguageMatchesFilter,
  buildLanguageNormalizer,
} from '../utils/languageFilters';
import { getNestedValue, toComparableText, toStringValue } from '../utils/objectHelpers';
import { toBookPlanPayload } from '../utils/packReview';
import { getReleaseFormats } from '../utils/releaseFormats';
import { INITIAL_ENTER_ANIMATION, nextEnterAnimation } from '../utils/releaseModalEnterAnimation';
import { buildReleaseDownloadPayload, type ReleaseDownloadOptions } from '../utils/releasePayload';
import {
  getBookTitleCandidates,
  getBookAuthorCandidates,
  sortReleasesByBookMatch,
} from '../utils/releaseScoring';
import type { SortState } from '../utils/releaseSort';
import {
  getSavedSort,
  saveSort,
  clearSort,
  inferDefaultDirection,
  sortReleases,
  FORMAT_SORT_KEY,
  sortReleasesByFormat,
} from '../utils/releaseSort';
import { BookDownloadButton } from './BookDownloadButton';
import { BookTargetDropdown } from './BookTargetDropdown';
import { Dropdown } from './Dropdown';
import { DropdownList } from './DropdownList';
import { LanguageMultiSelect } from './LanguageMultiSelect';
import { PackReviewPanel } from './PackReviewPanel';
import { ReleaseCell } from './ReleaseCell';

// Combined mode configuration for the ReleaseModal
interface CombinedModeConfig {
  phase: 'ebook' | 'audiobook';
  stepLabel: string;
  ebookMode: RequestPolicyMode;
  audiobookMode: RequestPolicyMode;
  stagedEbookRelease: Release | null;
  stagedAudiobookRelease: Release | null;
  onNext?: (release: Release | null) => void;
  onBack?: (audiobookRelease: Release | null) => void;
  onDownload?: (release: Release | null) => void;
  onClearSelection?: (contentType: ContentType) => void;
}

// Determine the combined download button label based on action modes
function getCombinedDownloadLabel(
  ebookMode: RequestPolicyMode | null | undefined,
  audiobookMode: RequestPolicyMode | null | undefined,
  hasEbookAction = true,
  hasAudiobookAction = true,
): string {
  if (hasEbookAction && !hasAudiobookAction) {
    return getSingleCombinedActionLabel('ebook', ebookMode);
  }
  if (!hasEbookAction && hasAudiobookAction) {
    return getSingleCombinedActionLabel('audiobook', audiobookMode);
  }

  const ebookIsRequest = ebookMode === 'request_release' || ebookMode === 'request_book';
  const audiobookIsRequest =
    audiobookMode === 'request_release' || audiobookMode === 'request_book';
  if (ebookIsRequest && audiobookIsRequest) return 'Request Both';
  if (ebookIsRequest || audiobookIsRequest) return 'Download & Request';
  return 'Download Both';
}

function getSingleCombinedActionLabel(
  contentType: ContentType,
  mode: RequestPolicyMode | null | undefined,
): string {
  const noun = contentType === 'ebook' ? 'Book' : 'Audiobook';
  const isRequest = mode === 'request_release' || mode === 'request_book';
  return `${isRequest ? 'Request' : 'Download'} ${noun}`;
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
      hide_mobile: false, // Language shown on mobile
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
      hide_mobile: false, // Format shown on mobile
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
      hide_mobile: false, // Size shown on mobile
      fallback: '-',
      uppercase: false,
    },
  ],
  grid_template: 'minmax(0,2fr) 60px 80px 80px',
  supported_filters: ['format', 'language'], // Default: both filters available
};

interface ReleaseModalProps {
  book: Book | null;
  onClose: () => void;
  onDownload: (
    book: Book,
    release: Release,
    contentType: ContentType,
    options?: ReleaseDownloadOptions,
  ) => Promise<void>;
  onRequestRelease?: (book: Book, release: Release, contentType: ContentType) => Promise<void>;
  onRequestBook?: (book: Book, contentType: ContentType) => Promise<void>;
  getPolicyModeForSource?: (source: string, contentType: ContentType) => RequestPolicyMode;
  supportedFormats: string[];
  supportedAudiobookFormats?: string[]; // Audiobook formats (m4b, mp3)
  contentType: ContentType; // 'ebook' or 'audiobook'
  defaultLanguages: string[];
  bookLanguages: Language[];
  currentStatus: StatusData;
  defaultReleaseSource?: string; // Default book tab to show (e.g., 'direct_download')
  defaultAudiobookReleaseSource?: string; // Default audiobook tab to show
  onSearchSeries?: (seriesName: string, seriesId?: string) => void; // Callback to search for series
  defaultShowManualQuery?: boolean;
  isRequestMode?: boolean;
  showReleaseSourceLinks?: boolean;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info') => void;
  // Combined mode (ebook + audiobook in one transaction)
  combinedMode?: CombinedModeConfig | null;
}

const STAR_POSITIONS = [0, 1, 2, 3, 4] as const;

interface ReleaseModalSessionProps extends Omit<ReleaseModalProps, 'book' | 'onClose'> {
  book: Book;
  isClosing: boolean;
  animateEnter: boolean;
  onClose: () => void;
}

// 5-star rating display with partial fill support
function StarRating({ rating, maxRating = 5 }: { rating: number; maxRating?: number }) {
  // Normalize rating to 0-5 scale if needed
  const normalizedRating = Math.min(Math.max(rating, 0), maxRating);

  return (
    <div className="flex items-center gap-0.5" title={`${rating} out of ${maxRating}`}>
      {STAR_POSITIONS.map((starPosition) => {
        const fillPercentage = Math.min(Math.max((normalizedRating - starPosition) * 100, 0), 100);

        return (
          <div key={starPosition} className="relative h-4 w-4">
            {/* Empty star (gray background) */}
            <svg
              className="absolute inset-0 h-4 w-4 text-zinc-300 dark:text-zinc-600"
              fill="currentColor"
              viewBox="0 0 20 20"
            >
              <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
            </svg>
            {/* Filled star (gold, clipped to fill percentage) */}
            <svg
              className="absolute inset-0 h-4 w-4 text-amber-400"
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
        className="flex h-10 w-7 shrink-0 items-center justify-center rounded-sm bg-zinc-200 text-[7px] font-medium text-zinc-500 sm:h-12 sm:w-8 sm:text-[8px] dark:bg-zinc-700 dark:text-zinc-400"
        aria-label="No cover available"
      >
        No Cover
      </div>
    );
  }

  return (
    <div className="relative h-10 w-7 shrink-0 overflow-hidden rounded-sm border border-white/40 bg-zinc-100 sm:h-12 sm:w-8 dark:border-zinc-700/70 dark:bg-zinc-800">
      {!imageLoaded && (
        <div className="absolute inset-0 animate-pulse bg-linear-to-r from-gray-200 via-gray-100 to-gray-200 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700" />
      )}
      <img
        src={preview}
        alt={title || 'Book cover'}
        className="h-full w-full object-cover object-top"
        loading="lazy"
        onLoad={() => setImageLoaded(true)}
        onError={() => setImageError(true)}
        style={{ opacity: imageLoaded ? 1 : 0, transition: 'opacity 0.2s ease-in-out' }}
      />
    </div>
  );
};

// Leading cell component - shows thumbnail, badge, or nothing based on config
const LeadingCell = ({ config, release }: { config?: LeadingCellConfig; release: Release }) => {
  // Default to thumbnail mode if no config
  const cellType = config?.type || 'thumbnail';

  if (cellType === 'none') {
    return null;
  }

  if (cellType === 'thumbnail') {
    const key = config?.key || 'extra.preview';
    const preview = toStringValue(getNestedValue(release, key));
    return <ReleaseThumbnail preview={preview} title={release.title} />;
  }

  // Badge type
  if (cellType === 'badge' && config?.key) {
    const displayValue = toComparableText(getNestedValue(release, config.key));
    const colorStyle = getColorStyleFromHint(displayValue, config.color_hint);
    const text = config.uppercase ? displayValue.toUpperCase() : displayValue;

    return (
      <div
        className={`h-10 w-7 rounded-lg sm:h-12 sm:w-8 ${colorStyle.bg} flex shrink-0 items-center justify-center`}
      >
        <span
          className={`text-[8px] font-bold sm:text-[9px] ${colorStyle.text} px-0.5 text-center leading-tight`}
        >
          {text}
        </span>
      </div>
    );
  }

  // Fallback
  return <ReleaseThumbnail preview={undefined} title={release.title} />;
};

// Radio indicator for selection mode
const RadioIndicator = ({ selected }: { selected: boolean }) => (
  <div className="flex h-8 w-8 shrink-0 items-center justify-center">
    <div
      className={`flex h-5 w-5 items-center justify-center rounded-full border-2 transition-colors ${
        selected ? 'border-emerald-500 bg-emerald-500' : 'border-zinc-300 dark:border-zinc-600'
      }`}
    >
      {selected && (
        <svg
          className="h-3 w-3 text-white"
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth="3"
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
        </svg>
      )}
    </div>
  </div>
);

// Phase indicator chip for combined mode footer
const PhaseChip = ({
  release,
  isActive,
  label,
  onClear,
}: {
  release: Release | null;
  isActive: boolean;
  label: string;
  onClear?: () => void;
}) => {
  let phaseChipClassName = 'bg-zinc-100 text-zinc-400 dark:bg-zinc-800 dark:text-zinc-500';
  if (release) {
    phaseChipClassName =
      'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-400';
  } else if (isActive) {
    phaseChipClassName = 'bg-zinc-100 text-(--text) dark:bg-zinc-800';
  }

  const selectedIcon = onClear ? (
    <span className="relative h-3 w-3 shrink-0">
      <svg
        className="absolute inset-0 h-3 w-3 opacity-100 transition-opacity group-hover:opacity-0 group-focus-visible:opacity-0"
        fill="none"
        viewBox="0 0 24 24"
        strokeWidth="2.5"
        stroke="currentColor"
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
      </svg>
      <svg
        className="absolute inset-0 h-3 w-3 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
        fill="none"
        viewBox="0 0 24 24"
        strokeWidth="2.5"
        stroke="currentColor"
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
      </svg>
    </span>
  ) : (
    <svg
      className="h-3 w-3"
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth="2.5"
      stroke="currentColor"
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
    </svg>
  );

  const chipContent = release ? (
    <>
      {selectedIcon}
      {release.format?.toUpperCase() || label} · {release.size || '?'}
    </>
  ) : (
    <>
      {isActive ? '\u25CF' : '\u25CB'} {label}
    </>
  );

  if (release && onClear) {
    return (
      <button
        type="button"
        onClick={onClear}
        className={`group inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium transition-colors hover:bg-red-50 hover:text-red-700 dark:hover:bg-red-900/20 dark:hover:text-red-300 ${phaseChipClassName}`}
        aria-label={`Remove selected ${label.toLowerCase()}`}
      >
        {chipContent}
      </button>
    );
  }

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${phaseChipClassName}`}
    >
      {chipContent}
    </span>
  );
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
  showReleaseSourceLinks,
  selectionMode = false,
  isSelected = false,
  onSelect,
}: {
  release: Release;
  index: number;
  onDownload: () => Promise<void>;
  buttonState: ButtonStateInfo;
  columns: ColumnSchema[];
  gridTemplate: string;
  leadingCell?: LeadingCellConfig;
  onlineServers?: string[];
  showReleaseSourceLinks: boolean;
  selectionMode?: boolean;
  isSelected?: boolean;
  onSelect?: () => void;
}) => {
  const author = toStringValue(release.extra?.author);

  // Filter columns visible on mobile
  const mobileColumns = columns.filter((c) => !c.hide_mobile);

  // Determine if leading cell should be shown
  // Default to showing thumbnail if no config provided, hide only if explicitly set to 'none'
  const showLeadingCell = leadingCell?.type !== 'none';

  // Build grid template based on whether leading cell is shown
  const desktopGridTemplate = showLeadingCell
    ? `auto 28px ${gridTemplate} auto`
    : `28px ${gridTemplate} auto`;

  const mobileGridTemplate = showLeadingCell ? 'auto 28px 1fr auto' : '28px 1fr auto';

  const handleRowClick = selectionMode && onSelect ? onSelect : undefined;
  const handleRowKeyDown: KeyboardEventHandler<HTMLDivElement> | undefined =
    selectionMode && onSelect
      ? (event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onSelect();
          }
        }
      : undefined;
  const selectionProps =
    selectionMode && onSelect
      ? {
          onClick: handleRowClick,
          onKeyDown: handleRowKeyDown,
          role: 'option' as const,
          'aria-selected': isSelected,
          tabIndex: 0,
        }
      : undefined;

  return (
    <div
      className={`hover-row animate-pop-up py-2 pr-4 pl-5 transition-colors duration-200 sm:pr-5 ${
        selectionMode ? 'cursor-pointer' : ''
      } ${isSelected ? 'bg-emerald-50/50 dark:bg-emerald-900/10' : ''}`}
      style={{
        animationDelay: `${index * 30}ms`,
        animationFillMode: 'both',
      }}
      {...selectionProps}
    >
      {/* Desktop layout with dynamic grid */}
      <div
        className="hidden items-center gap-3 sm:grid"
        style={{ gridTemplateColumns: desktopGridTemplate }}
      >
        {/* Leading cell: Thumbnail, Badge, or nothing */}
        {showLeadingCell && <LeadingCell config={leadingCell} release={release} />}

        {/* Release number */}
        <span className="w-7 shrink-0 text-center text-xs font-medium text-zinc-400 dark:text-zinc-500">
          {index + 1}
        </span>

        {/* Fixed: Title and author */}
        <div className="min-w-0">
          <p className="line-clamp-2 text-sm font-medium" title={release.title}>
            {showReleaseSourceLinks && release.info_url ? (
              <a
                href={release.info_url}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:text-emerald-600 hover:underline dark:hover:text-emerald-400"
                onClick={(e) => e.stopPropagation()}
              >
                {release.title}
              </a>
            ) : (
              release.title
            )}
          </p>
          {author && <p className="truncate text-xs text-zinc-500 dark:text-zinc-400">{author}</p>}
        </div>

        {/* Dynamic columns from schema */}
        {columns.map((col) => (
          <ReleaseCell key={col.key} column={col} release={release} onlineServers={onlineServers} />
        ))}

        {/* Fixed: Action button or radio indicator */}
        {selectionMode ? (
          <RadioIndicator selected={isSelected} />
        ) : (
          <BookDownloadButton
            buttonState={buttonState}
            onDownload={onDownload}
            variant="icon"
            size="sm"
            ariaLabel={`${buttonState.text} ${release.title}`}
          />
        )}
      </div>

      {/* Mobile layout - author inline with title, info line below */}
      <div
        className="grid items-center gap-2 sm:hidden"
        style={{ gridTemplateColumns: mobileGridTemplate }}
      >
        {/* Leading cell: Thumbnail, Badge, or nothing */}
        {showLeadingCell && <LeadingCell config={leadingCell} release={release} />}

        {/* Release number */}
        <span className="shrink-0 text-center text-xs font-medium text-zinc-400 dark:text-zinc-500">
          {index + 1}
        </span>

        <div className="min-w-0">
          {/* Title and author on same line */}
          <p className="line-clamp-2 text-sm leading-tight" title={release.title}>
            {showReleaseSourceLinks && release.info_url ? (
              <a
                href={release.info_url}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium hover:text-emerald-600 hover:underline dark:hover:text-emerald-400"
                onClick={(e) => e.stopPropagation()}
              >
                {release.title}
              </a>
            ) : (
              <span className="font-medium">{release.title}</span>
            )}
            {author && (
              <span className="font-normal text-zinc-500 dark:text-zinc-400"> — {author}</span>
            )}
          </p>
          {/* Plugin-provided info line (format, size, indexer, seeders, etc.) */}
          {mobileColumns.length > 0 && (
            <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[10px] text-zinc-500 dark:text-zinc-400">
              {(() => {
                // Pre-filter columns that will render content to avoid orphan dots
                const columnsWithContent = mobileColumns.filter((col) => {
                  const rawValue = getNestedValue(release, col.key);
                  const value =
                    rawValue !== undefined && rawValue !== null
                      ? toComparableText(rawValue)
                      : col.fallback;

                  if (col.render_type === 'flag_icon') {
                    // flag_icon returns null in compact mode when empty
                    if (!value || value === col.fallback) {
                      return false;
                    }
                  }

                  if (col.render_type === 'tags') {
                    // tags render null in compact mode when empty and no fallback
                    if (Array.isArray(rawValue)) {
                      return rawValue.some((tag) => String(tag).trim()) || Boolean(col.fallback);
                    }
                    if (!value || value === col.fallback) {
                      return Boolean(col.fallback);
                    }
                  }

                  if (col.render_type === 'text' || col.render_type === 'number') {
                    // Optional detail columns (series, narrator, bitrate) are often blank
                    return value.trim() !== '';
                  }

                  return true;
                });
                return columnsWithContent.map((col, idx) => (
                  <span key={col.key} className="flex items-center gap-1.5">
                    {idx > 0 && <span className="text-zinc-300 dark:text-zinc-600">·</span>}
                    <ReleaseCell
                      column={col}
                      release={release}
                      compact
                      onlineServers={onlineServers}
                    />
                  </span>
                ));
              })()}
            </div>
          )}
        </div>

        {selectionMode ? (
          <RadioIndicator selected={isSelected} />
        ) : (
          <BookDownloadButton
            buttonState={buttonState}
            onDownload={onDownload}
            variant="icon"
            size="sm"
            ariaLabel={`${buttonState.text} ${release.title}`}
          />
        )}
      </div>
    </div>
  );
};

// Shimmer block with wave animation
function ShimmerBlock({ className }: { className: string }) {
  return (
    <div
      className={`relative overflow-hidden rounded-sm bg-zinc-200 dark:bg-zinc-800 ${className}`}
    >
      <div
        className="absolute inset-0 dark:opacity-50"
        style={{
          background:
            'linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.4) 50%, transparent 100%)',
          backgroundSize: '200% 100%',
          animation: 'wave 2s ease-in-out infinite',
        }}
      />
    </div>
  );
}

// Loading skeleton for releases - matches ReleaseRow layout
// Renders enough rows to fill the container, fading out at the bottom via a gradient mask
function ReleaseSkeleton() {
  // Render enough rows to cover tall viewports; overflow is hidden by the mask
  const rows = 8;
  return (
    <div
      className="divide-y divide-zinc-200/60 overflow-hidden dark:divide-zinc-800/60"
      style={{
        maskImage: 'linear-gradient(to bottom, black 40%, transparent 100%)',
        WebkitMaskImage: 'linear-gradient(to bottom, black 40%, transparent 100%)',
      }}
    >
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="px-5 py-2">
          <div className="grid grid-cols-[auto_1fr_auto] items-center gap-2 sm:grid-cols-[auto_minmax(0,2fr)_60px_80px_80px_auto] sm:gap-3">
            {/* Thumbnail skeleton */}
            <ShimmerBlock className="h-10 w-7 sm:h-14 sm:w-10" />

            {/* Title and author skeleton */}
            <div className="min-w-0 space-y-1.5">
              <ShimmerBlock className="h-4 w-3/4" />
              <ShimmerBlock className="h-3 w-1/2" />
            </div>

            {/* Language badge skeleton - desktop */}
            <div className="hidden justify-center sm:flex">
              <ShimmerBlock className="h-5 w-8" />
            </div>

            {/* Format badge skeleton - desktop */}
            <div className="hidden justify-center sm:flex">
              <ShimmerBlock className="h-5 w-12" />
            </div>

            {/* Size skeleton - desktop */}
            <div className="hidden justify-center sm:flex">
              <ShimmerBlock className="h-4 w-14" />
            </div>

            {/* Mobile info + action skeleton */}
            <div className="flex items-center gap-2">
              {/* Mobile: format + size inline */}
              <div className="flex flex-col items-end gap-1 sm:hidden">
                <ShimmerBlock className="h-3 w-10" />
                <ShimmerBlock className="h-3 w-14" />
              </div>

              {/* Action button skeleton */}
              <ShimmerBlock className="h-8 w-8 rounded-full!" />
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
    <div className="px-4 py-12 text-center">
      <div className="mb-4 inline-flex h-14 w-14 items-center justify-center rounded-full bg-zinc-100 dark:bg-zinc-800">
        <svg
          className="h-7 w-7 text-zinc-400"
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
      <p className="text-sm text-zinc-500 dark:text-zinc-400">{message}</p>
    </div>
  );
}

// Error state component
function ErrorState({ message }: { message: string }) {
  return (
    <div className="px-4 py-12 text-center">
      <div className="mb-4 inline-flex h-14 w-14 items-center justify-center rounded-full bg-red-100 dark:bg-red-900/30">
        <svg
          className="h-7 w-7 text-red-600 dark:text-red-400"
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
      <h4 className="mb-2 text-base font-semibold text-zinc-900 dark:text-zinc-100">
        Error Loading Releases
      </h4>
      <p className="mx-auto max-w-xs text-sm text-zinc-500 dark:text-zinc-400">{message}</p>
    </div>
  );
}

const EMPTY_SUPPORTED_AUDIOBOOK_FORMATS: string[] = [];

const ReleaseModalSession = ({
  book,
  onClose,
  onDownload,
  onRequestRelease,
  onRequestBook,
  getPolicyModeForSource,
  supportedFormats,
  supportedAudiobookFormats = EMPTY_SUPPORTED_AUDIOBOOK_FORMATS,
  contentType,
  defaultLanguages,
  bookLanguages,
  currentStatus,
  defaultReleaseSource,
  defaultAudiobookReleaseSource,
  onSearchSeries,
  defaultShowManualQuery = false,
  isRequestMode = false,
  showReleaseSourceLinks = true,
  onShowToast,
  combinedMode = null,
  isClosing,
  animateEnter,
}: ReleaseModalSessionProps) => {
  // Use audiobook formats when in audiobook mode
  const effectiveFormats =
    contentType === 'audiobook' && supportedAudiobookFormats.length > 0
      ? supportedAudiobookFormats
      : supportedFormats;
  const [isRequestingBook, setIsRequestingBook] = useState(false);
  const [selectedRelease, setSelectedRelease] = useState<Release | null>(null);
  // Multi-book packs: `multiBook` is the manual header toggle (heuristic split for
  // releases we can't inspect); `packReview` holds an inspected pack awaiting approval.
  const [multiBook, setMultiBook] = useState(false);
  const [packReview, setPackReview] = useState<{
    release: Release;
    plan: PackPlan;
    books: PackBook[];
  } | null>(null);
  const [packSubmitting, setPackSubmitting] = useState(false);
  const isCombinedMode = combinedMode != null;
  const combinedPhase = combinedMode?.phase ?? null;
  const combinedStepLabel = combinedMode?.stepLabel ?? '';
  const combinedEbookMode = combinedMode?.ebookMode ?? null;
  const combinedAudiobookMode = combinedMode?.audiobookMode ?? null;
  const stagedEbookRelease = combinedMode?.stagedEbookRelease ?? null;
  const stagedAudiobookRelease = combinedMode?.stagedAudiobookRelease ?? null;
  let stagedReleaseForPhase: Release | null = null;
  if (combinedPhase === 'ebook') {
    stagedReleaseForPhase = stagedEbookRelease;
  } else if (combinedPhase === 'audiobook') {
    stagedReleaseForPhase = stagedAudiobookRelease;
  }
  const onCombinedNext = combinedMode?.onNext;
  const onCombinedBack = combinedMode?.onBack;
  const onCombinedDownload = combinedMode?.onDownload;
  const onCombinedClearSelection = combinedMode?.onClearSelection;

  const handleClose = onClose;

  const {
    sourcesLoading,
    sourcesError,
    activeTab,
    setActiveTab,
    allTabs,
    releasesBySource,
    loadingBySource,
    errorBySource,
    expandedBySource,
    searchStatus,
    formatFilter,
    setFormatFilter,
    languageFilter,
    setLanguageFilter,
    indexerFilter,
    setIndexerFilter,
    manualQuery,
    setManualQuery,
    showManualQuery,
    toggleManualQuery,
    applyCurrentFilters,
    runManualSearch,
    expandSearch,
    isIndexerFilterInitialized,
  } = useReleaseSearchSession({
    book,
    contentType,
    defaultReleaseSource,
    defaultAudiobookReleaseSource,
    defaultShowManualQuery,
    bookLanguages,
    defaultLanguages,
  });

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const bookSummaryRef = useRef<HTMLDivElement>(null);
  const showHeaderThumb = useHeaderThumbOnScroll({ scrollContainerRef, bookSummaryRef });

  // Sort state - keyed by source name, persisted to localStorage
  // null means "Default" (best title match), undefined means "not set yet"
  const [sortBySource, setSortBySource] = useState<Record<string, SortState | null>>({});
  const [formatSortExpanded, setFormatSortExpanded] = useState(false);

  // Description expansion
  const [descriptionExpanded, setDescriptionExpanded] = useState(false);
  const descriptionRef = useRef<HTMLParagraphElement>(null);
  const descriptionOverflows = useDescriptionOverflow({
    descriptionRef,
    descriptionExpanded,
    descriptionKey: book.description,
  });
  const activeBookId = book?.id;
  const [appliedCombinedSelection, setAppliedCombinedSelection] = useState<{
    bookId: Book['id'] | null;
    phase: CombinedModeConfig['phase'] | null;
    release: Release | null;
  }>({
    bookId: null,
    phase: null,
    release: null,
  });
  if (
    isCombinedMode &&
    (appliedCombinedSelection.bookId !== (activeBookId ?? null) ||
      appliedCombinedSelection.phase !== combinedPhase ||
      appliedCombinedSelection.release !== stagedReleaseForPhase)
  ) {
    setSelectedRelease(stagedReleaseForPhase);
    setAppliedCombinedSelection({
      bookId: activeBookId ?? null,
      phase: combinedPhase,
      release: stagedReleaseForPhase,
    });
  }

  const handleRequestBook = useCallback(async (): Promise<void> => {
    if (!book || !onRequestBook || isRequestingBook) {
      return;
    }
    setIsRequestingBook(true);
    try {
      await onRequestBook(book, contentType);
      handleClose();
    } finally {
      setIsRequestingBook(false);
    }
    // Kept against the advisory: the body really does read both. `handleClose` is aliased
    // from the `onClose` prop, which is why the compiler names the source instead, and
    // dropping `contentType` would let this close over a stale one and request the wrong
    // format. Correctness first; the cost is an extra callback identity.
    // oxlint-disable-next-line react/memo-dependencies
  }, [book, onRequestBook, isRequestingBook, contentType, handleClose]);

  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const tabIndicatorStyle = useTabIndicator(tabRefs, activeTab, allTabs);

  // Get unique formats from current releases for filter dropdown
  // Only show formats that are in the supported list
  const availableFormats = useMemo(() => {
    const releases = releasesBySource[activeTab]?.releases || [];
    const formats = new Set<string>();
    const effectiveLower = new Set(effectiveFormats.map((f) => f.toLowerCase()));

    releases.forEach((r) => {
      const releaseFormats = getReleaseFormats(r);
      releaseFormats.forEach((fmt) => {
        // Only include formats that are in the supported list
        if (effectiveLower.has(fmt)) {
          formats.add(fmt);
        }
      });
    });
    return Array.from(formats).toSorted();
  }, [releasesBySource, activeTab, effectiveFormats]);

  // Build select options for format filter
  const formatOptions = useMemo(() => {
    const options = [{ value: '', label: 'All Formats' }];
    availableFormats.forEach((fmt) => {
      options.push({ value: fmt, label: fmt.toUpperCase() });
    });
    return options;
  }, [availableFormats]);

  // Get available indexers for filter dropdown
  // Prefer available_indexers from column config (all enabled Prowlarr indexers),
  // fall back to unique indexers from results if not provided
  const availableIndexers = useMemo(() => {
    // Use column config's available_indexers if provided (e.g., from Prowlarr)
    const configIndexers = releasesBySource[activeTab]?.column_config?.available_indexers;
    if (configIndexers && configIndexers.length > 0) {
      return configIndexers;
    }
    // Fall back to indexers found in results
    const releases = releasesBySource[activeTab]?.releases || [];
    const indexers = new Set<string>();
    releases.forEach((r) => {
      if (r.indexer) {
        indexers.add(r.indexer);
      }
    });
    return Array.from(indexers).toSorted();
  }, [releasesBySource, activeTab]);

  // Resolve language filter to actual language codes for filtering
  const resolvedLanguageCodes = useMemo(() => {
    return getLanguageFilterValues(languageFilter, bookLanguages, defaultLanguages);
  }, [languageFilter, bookLanguages, defaultLanguages]);

  // Build language normalizer for release filtering (handles both codes like "en" and names like "English")
  const languageNormalizer = useMemo(() => {
    return buildLanguageNormalizer(bookLanguages);
  }, [bookLanguages]);

  // Get column config from response or use default (moved before filteredReleases for sorting)
  const columnConfig = useMemo((): ReleaseColumnConfig => {
    const response = releasesBySource[activeTab];
    if (response?.column_config) {
      return response.column_config;
    }
    return DEFAULT_COLUMN_CONFIG;
  }, [releasesBySource, activeTab]);

  // Get sortable columns from column config
  const sortableColumns = useMemo(() => {
    return columnConfig.columns.filter((col) => col.sortable) || [];
  }, [columnConfig]);

  // Build unified list of all sort options (from sortable columns + extra_sort_options)
  const allSortOptions = useMemo(() => {
    const fromColumns = sortableColumns.map((col) => ({
      label: col.label,
      sortKey: col.sort_key || col.key,
      defaultDirection: inferDefaultDirection(col.render_type),
    }));
    const fromExtra = (columnConfig.extra_sort_options || []).map((opt) => ({
      label: opt.label,
      sortKey: opt.sort_key,
      defaultDirection: opt.default_direction ?? ('desc' as const),
    }));
    return [...fromColumns, ...fromExtra];
  }, [sortableColumns, columnConfig.extra_sort_options]);

  const isValidSortForCurrentResults = useCallback(
    (sort: SortState | null): boolean => {
      if (!sort) return false;

      if (sort.key === FORMAT_SORT_KEY) {
        return !!sort.value && availableFormats.includes(sort.value);
      }

      return allSortOptions.some((opt) => opt.sortKey === sort.key);
    },
    [availableFormats, allSortOptions],
  );

  // Get current sort state for active tab (from state, localStorage, or default to null = best match)
  const currentSort = useMemo((): SortState | null => {
    // Check state first - explicit null means "Default" was selected
    if (activeTab in sortBySource) {
      const inMemory = sortBySource[activeTab];
      return inMemory === null || isValidSortForCurrentResults(inMemory) ? inMemory : null;
    }
    // Check localStorage
    const saved = getSavedSort(activeTab);
    if (isValidSortForCurrentResults(saved)) {
      return saved;
    }
    // Default to null (best-match sorting)
    return null;
  }, [activeTab, sortBySource, isValidSortForCurrentResults]);

  // Handle sort change - null means "Default" (best title match), otherwise toggle direction or set new column
  const handleSortChange = useCallback(
    (sortKey: string | null, defaultDirection: 'asc' | 'desc', value?: string) => {
      if (sortKey === null) {
        // "Default" selected - use best-match sorting
        setSortBySource((prev) => {
          const next = { ...prev };
          delete next[activeTab];
          return next;
        });
        clearSort(activeTab);
        return;
      }

      const currentState = sortBySource[activeTab] ?? currentSort;
      let newState: SortState;

      const isSameSort =
        currentState && currentState.key === sortKey && currentState.value === value;
      if (isSameSort) {
        // Same key+value - toggle direction
        newState = {
          key: sortKey,
          direction: currentState.direction === 'asc' ? 'desc' : 'asc',
          ...(value !== undefined && { value }),
        };
      } else {
        // New key or different value - use provided default direction
        newState = {
          key: sortKey,
          direction: defaultDirection,
          ...(value !== undefined && { value }),
        };
      }

      setSortBySource((prev) => ({ ...prev, [activeTab]: newState }));
      saveSort(activeTab, newState);
    },
    [activeTab, sortBySource, currentSort],
  );

  // Filter and sort releases based on settings and user selection
  const filteredReleases = useMemo(() => {
    const releases = releasesBySource[activeTab]?.releases || [];
    const effectiveLower = new Set(effectiveFormats.map((f) => f.toLowerCase()));
    const supportsIndexerFilter = columnConfig.supported_filters?.includes('indexer');
    const selectedFormat = formatFilter.toLowerCase();

    // First, filter
    let filtered = releases.filter((r) => {
      // Format filtering
      const releaseFormats = getReleaseFormats(r);

      if (formatFilter) {
        // User selected a specific format - match if any format on the release matches
        if (!releaseFormats.includes(selectedFormat)) return false;
      } else if (releaseFormats.length > 0) {
        // No specific filter - show only releases that include at least one supported format
        if (!releaseFormats.some((fmt) => effectiveLower.has(fmt))) return false;
      }
      // Releases with no format info pass through when no filter is set (show all)

      // Language filtering - use r.language when provided by enriched indexers
      // Releases with no language (null/undefined) always pass
      const releaseLang = r.language;
      if (
        !releaseLanguageMatchesFilter(
          releaseLang,
          resolvedLanguageCodes ?? defaultLanguages,
          languageNormalizer,
        )
      ) {
        return false;
      }

      // Indexer filtering - empty array means show all
      if (supportsIndexerFilter && indexerFilter.length > 0 && r.indexer) {
        if (!indexerFilter.includes(r.indexer)) {
          return false;
        }
      }

      return true;
    });

    // Then, sort by explicit column/format, or default to book-title relevance with exact author boost
    if (currentSort?.key === FORMAT_SORT_KEY && currentSort.value) {
      filtered = sortReleasesByFormat(filtered, currentSort.value, currentSort.direction);
    } else if (currentSort && allSortOptions.length > 0) {
      filtered = sortReleases(filtered, currentSort.key, currentSort.direction);
    } else {
      const responseBook = releasesBySource[activeTab]?.book;
      const titleCandidates = getBookTitleCandidates(book, responseBook);
      const authorCandidates = getBookAuthorCandidates(book, responseBook);
      filtered = sortReleasesByBookMatch(filtered, titleCandidates, authorCandidates);
    }

    return filtered;
  }, [
    releasesBySource,
    activeTab,
    formatFilter,
    resolvedLanguageCodes,
    effectiveFormats,
    defaultLanguages,
    languageNormalizer,
    indexerFilter,
    currentSort,
    allSortOptions,
    columnConfig,
    book,
  ]);

  // Pre-compute display field lookups to avoid repeated .find() calls in JSX
  const displayFields = useMemo(() => {
    if (!book?.display_fields) return null;

    const starField = book.display_fields.find((f) => f.icon === 'star');
    const ratingsField = book.display_fields.find((f) => f.icon === 'ratings');
    const usersField = book.display_fields.find((f) => f.icon === 'users');
    const pagesField = book.display_fields.find((f) => f.icon === 'book');
    const lengthField = book.display_fields.find((f) => f.icon === 'clock');
    const narratorField = book.display_fields.find((f) => f.icon === 'microphone');

    return { starField, ratingsField, usersField, pagesField, lengthField, narratorField };
    // `book`, not `book?.display_fields`: the body reads `book.display_fields`
    // unguarded after the early return, which is the dependency the compiler infers.
  }, [book]);

  const getReleaseActionMode = useCallback(
    (release: Release): RequestPolicyMode => {
      if (!getPolicyModeForSource) {
        return 'download';
      }
      return getPolicyModeForSource(release.source, contentType);
    },
    [getPolicyModeForSource, contentType],
  );

  // Get button state for a release row (queue state + policy mode).
  const getButtonState = useCallback(
    (release: Release): ButtonStateInfo => {
      const releaseId = release.source_id;
      const mode = getReleaseActionMode(release);
      // Check error first
      if (currentStatus.error && currentStatus.error[releaseId]) {
        if (mode === 'request_release') {
          return { text: 'Request', state: 'download' };
        }
        if (mode === 'blocked' || mode === 'request_book') {
          return { text: 'Unavailable', state: 'blocked' };
        }
        return currentStatus.error[releaseId].retry_available === true
          ? { text: 'Retry', state: 'download' }
          : { text: 'Failed', state: 'error' };
      }
      // Check completed
      if (currentStatus.complete && currentStatus.complete[releaseId]) {
        return { text: 'Downloaded', state: 'complete' };
      }
      // Check in-progress states
      if (currentStatus.downloading && currentStatus.downloading[releaseId]) {
        const downloadingBook = currentStatus.downloading[releaseId];
        return {
          text: 'Downloading',
          state: 'downloading',
          progress: downloadingBook.progress,
        };
      }
      if (currentStatus.locating && currentStatus.locating[releaseId]) {
        return { text: 'Locating files', state: 'locating' };
      }
      if (currentStatus.resolving && currentStatus.resolving[releaseId]) {
        return { text: 'Resolving', state: 'resolving' };
      }
      if (currentStatus.queued && currentStatus.queued[releaseId]) {
        return { text: 'Queued', state: 'queued' };
      }
      if (mode === 'request_release') {
        return { text: 'Request', state: 'download' };
      }
      if (mode === 'blocked' || mode === 'request_book') {
        return { text: 'Unavailable', state: 'blocked' };
      }
      return { text: 'Download', state: 'download' };
    },
    [currentStatus, getReleaseActionMode],
  );

  // Handle row action based on resolved policy mode.
  const handleReleaseAction = useCallback(
    async (release: Release): Promise<void> => {
      if (!book) {
        return;
      }

      // In combined mode, clicking a row selects it (don't download)
      if (isCombinedMode) {
        const mode = getReleaseActionMode(release);
        if (mode === 'blocked' || mode === 'request_book') {
          return;
        }
        setSelectedRelease(release);
        return;
      }

      const mode = getReleaseActionMode(release);
      if (mode === 'download') {
        // Look at the release's files before queueing so a whole-series pack can be
        // reviewed and filed as separate books instead of one mangled item.
        let inspected = false;
        let plan: PackPlan | null = null;
        let reason: string | null = null;
        try {
          const inspection = await inspectRelease(
            buildReleaseDownloadPayload(book, release, contentType),
          );
          inspected = inspection.inspected;
          plan = inspection.plan;
          reason = inspection.reason;
        } catch (error) {
          console.error('Release inspection failed:', error);
        }
        if (inspected && plan?.is_pack) {
          setPackReview({ release, plan, books: plan.books });
          return;
        }
        // Not a pack (or couldn't be inspected): queue exactly as before. A release we
        // couldn't inspect might still be an unnoticed pack, so leave a console breadcrumb
        // rather than interrupting the user; the multi-book toggle forces the split.
        if (!inspected && !multiBook) {
          console.warn(
            `Could not inspect release "${release.title}" before download${
              reason ? `: ${reason}` : ''
            }. If it contains several books, enable the multi-book pack toggle.`,
          );
        }
        await onDownload(book, release, contentType, multiBook ? { multiBook: true } : {});
        handleClose();
        return;
      }
      if (mode === 'request_release') {
        if (onRequestRelease) {
          await onRequestRelease(book, release, contentType);
          handleClose();
        }
      }
    },
    [
      book,
      isCombinedMode,
      getReleaseActionMode,
      onDownload,
      onRequestRelease,
      contentType,
      handleClose,
      multiBook,
    ],
  );

  const handlePackConfirm = useCallback(
    async (books: PackBook[] | null): Promise<void> => {
      if (!book || !packReview) {
        return;
      }
      setPackSubmitting(true);
      try {
        await onDownload(
          book,
          packReview.release,
          contentType,
          books ? { multiBook: true, bookPlan: toBookPlanPayload(books) } : {},
        );
        handleClose();
      } finally {
        setPackSubmitting(false);
      }
    },
    // Same as handleRequestBook above: the body reads `onDownload`, `contentType` and
    // `handleClose`, so they stay in the list whatever the advisory infers.
    // oxlint-disable-next-line react/memo-dependencies
    [book, packReview, onDownload, contentType, handleClose],
  );

  const titleId = `release-modal-title-${book.id}`;
  const providerDisplay =
    book.provider_display_name ||
    (book.provider ? book.provider.charAt(0).toUpperCase() + book.provider.slice(1) : 'Unknown');
  const showBookSourceLink =
    Boolean(book.source_url) && (isMetadataBook(book) || showReleaseSourceLinks);

  const currentTabLoading = loadingBySource[activeTab] ?? false;
  const currentTabError = errorBySource[activeTab] ?? null;
  const hasActiveTab = activeTab.length > 0;
  const isInitialLoading =
    hasActiveTab &&
    (currentTabLoading || (releasesBySource[activeTab] === undefined && !currentTabError));
  const coverAspectClassName = book.cover_aspect === 'square' ? 'object-center' : 'object-top';

  let coverSizeClassName = 'h-[120px] w-20';
  if (book.cover_aspect === 'square') {
    coverSizeClassName = book.series_name ? 'h-[144px] w-[144px]' : 'h-[120px] w-[120px]';
  } else if (book.series_name) {
    coverSizeClassName = 'h-[144px] w-24';
  }

  let combinedFooterEbookMode = combinedEbookMode;
  if (combinedPhase === 'ebook') {
    combinedFooterEbookMode = selectedRelease
      ? getReleaseActionMode(selectedRelease)
      : combinedEbookMode;
  } else if (stagedEbookRelease) {
    combinedFooterEbookMode = getReleaseActionMode(stagedEbookRelease);
  }

  let combinedFooterAudiobookMode = combinedAudiobookMode;
  if (combinedPhase === 'audiobook') {
    combinedFooterAudiobookMode = selectedRelease
      ? getReleaseActionMode(selectedRelease)
      : combinedAudiobookMode;
  } else if (stagedAudiobookRelease) {
    combinedFooterAudiobookMode = getReleaseActionMode(stagedAudiobookRelease);
  }

  const hasCombinedEbookAction =
    combinedPhase === 'ebook'
      ? selectedRelease !== null
      : stagedEbookRelease !== null || combinedFooterEbookMode === 'request_book';
  const hasCombinedAudiobookAction =
    combinedPhase === 'audiobook'
      ? selectedRelease !== null
      : stagedAudiobookRelease !== null || combinedFooterAudiobookMode === 'request_book';
  const hasCombinedActionOutsideCurrentPhase =
    combinedPhase === 'ebook' ? hasCombinedAudiobookAction : hasCombinedEbookAction;
  const canCompleteCombinedAction =
    selectedRelease !== null || hasCombinedActionOutsideCurrentPhase;
  const currentCombinedPhaseLabel = combinedPhase === 'ebook' ? 'Book' : 'Audiobook';
  const nextCombinedPhaseLabel = combinedPhase === 'ebook' ? 'Audiobook' : 'Book';
  let emptyStateMessage = 'No releases found for this book.';
  if (formatFilter) {
    emptyStateMessage = `No ${formatFilter.toUpperCase()} releases found. Try a different format.`;
  } else if (isCombinedMode) {
    emptyStateMessage = `No ${currentCombinedPhaseLabel.toLowerCase()} releases found.`;
  }
  let modalAnimationClassName = '';
  if (isClosing) {
    modalAnimationClassName = 'settings-modal-exit';
  } else if (animateEnter) {
    modalAnimationClassName = 'settings-modal-enter';
  } else {
    modalAnimationClassName = 'release-modal-no-step-animation';
  }
  const canClearEbookSelection =
    combinedPhase === 'ebook' ? selectedRelease !== null : stagedEbookRelease !== null;
  const canClearAudiobookSelection =
    combinedPhase === 'audiobook' ? selectedRelease !== null : stagedAudiobookRelease !== null;
  const clearEbookSelection = canClearEbookSelection
    ? () => {
        if (combinedPhase === 'ebook') {
          setSelectedRelease(null);
        }
        onCombinedClearSelection?.('ebook');
      }
    : undefined;
  const clearAudiobookSelection = canClearAudiobookSelection
    ? () => {
        if (combinedPhase === 'audiobook') {
          setSelectedRelease(null);
        }
        onCombinedClearSelection?.('audiobook');
      }
    : undefined;

  const modal = (
    <div className="modal-overlay active sm:px-6 sm:py-6">
      <button
        type="button"
        className="absolute inset-0 border-0 bg-transparent p-0"
        onClick={handleClose}
        aria-label="Close release modal"
      />
      <div
        className={`details-container relative z-10 h-full w-full sm:h-auto ${modalAnimationClassName}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex h-full flex-col overflow-hidden rounded-none border-0 border-(--border-muted) bg-(--bg) text-(--text) shadow-none sm:h-[90vh] sm:max-h-[90vh] sm:rounded-2xl sm:border sm:bg-(--bg-soft) sm:shadow-2xl">
          {/* Header */}
          <header className="flex items-start gap-3 border-b border-(--border-muted) px-5 py-4">
            {/* Mobile: static thumbnail always visible */}
            {!isRequestMode && (
              <div className="shrink-0 sm:hidden">
                {book.preview ? (
                  <img
                    src={book.preview}
                    alt=""
                    width={book.cover_aspect === 'square' ? 68 : 46}
                    height={68}
                    className={`rounded-sm object-cover shadow-md ${book.cover_aspect === 'square' ? 'object-center' : 'object-top'}`}
                    style={{
                      width: book.cover_aspect === 'square' ? 68 : 46,
                      height: 68,
                      minWidth: book.cover_aspect === 'square' ? 68 : 46,
                    }}
                  />
                ) : (
                  <div
                    className="flex items-center justify-center rounded-sm border border-dashed border-(--border-muted) bg-(--bg)/60 text-[7px] text-zinc-500"
                    style={{
                      width: book.cover_aspect === 'square' ? 68 : 46,
                      height: 68,
                      minWidth: book.cover_aspect === 'square' ? 68 : 46,
                    }}
                  >
                    No cover
                  </div>
                )}
              </div>
            )}
            {/* Desktop: animated thumbnail that appears when scrolling */}
            {!isRequestMode && (
              <div
                className="hidden shrink-0 overflow-hidden transition-[width,margin] duration-300 ease-out sm:block"
                style={{
                  width: showHeaderThumb ? (book.cover_aspect === 'square' ? 68 : 46) : 0,
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
                      width={book.cover_aspect === 'square' ? 68 : 46}
                      height={68}
                      className={`rounded-sm object-cover shadow-md ${book.cover_aspect === 'square' ? 'object-center' : 'object-top'}`}
                      style={{
                        width: book.cover_aspect === 'square' ? 68 : 46,
                        height: 68,
                        minWidth: book.cover_aspect === 'square' ? 68 : 46,
                      }}
                    />
                  ) : (
                    <div
                      className="flex items-center justify-center rounded-sm border border-dashed border-(--border-muted) bg-(--bg)/60 text-[7px] text-zinc-500"
                      style={{
                        width: book.cover_aspect === 'square' ? 68 : 46,
                        height: 68,
                        minWidth: book.cover_aspect === 'square' ? 68 : 46,
                      }}
                    >
                      No cover
                    </div>
                  )}
                </div>
              </div>
            )}
            <div className="min-w-0 flex-1 space-y-1">
              <p className="text-xs tracking-wide text-zinc-500 uppercase dark:text-zinc-400">
                {isCombinedMode ? combinedStepLabel : 'Find Releases'}
              </p>
              <h3 id={titleId} className="truncate text-lg leading-snug font-semibold">
                {book.provider === 'manual' ? 'Manual Query' : book.title || 'Untitled'}
              </h3>
              {!isRequestMode && (
                <p className="truncate text-sm text-zinc-600 dark:text-zinc-300">
                  {book.author || 'Unknown author'}
                </p>
              )}
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <button
                type="button"
                onClick={handleClose}
                className="hover-action rounded-full p-2 text-zinc-500 transition-colors hover:text-zinc-900 dark:hover:text-zinc-100"
                aria-label="Close"
              >
                <svg
                  className="h-5 w-5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  strokeWidth={1.5}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </header>

          {/* Scrollable content */}
          <div ref={scrollContainerRef} className="min-h-0 flex-1 overflow-y-auto">
            {/* Book summary - scrolls with content */}
            {!isRequestMode && (
              <div
                ref={bookSummaryRef}
                className="flex gap-4 border-b border-(--border-muted) px-5 py-4"
              >
                {book.preview ? (
                  <img
                    src={book.preview}
                    alt="Book cover"
                    className={`hidden shrink-0 rounded-lg object-cover shadow-md sm:block ${coverAspectClassName} ${coverSizeClassName}`}
                  />
                ) : (
                  <div
                    className={`hidden shrink-0 items-center justify-center rounded-lg border border-dashed border-(--border-muted) bg-(--bg)/60 text-[10px] text-zinc-500 sm:flex ${coverSizeClassName}`}
                  >
                    No cover
                  </div>
                )}
                <div className="flex min-w-0 flex-1 flex-col gap-2">
                  {/* Metadata row */}
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-zinc-600 dark:text-zinc-400">
                    {book.year && <span>{book.year}</span>}
                    {displayFields?.starField && (
                      <span className="flex items-center gap-1.5">
                        <StarRating rating={parseFloat(displayFields.starField.value || '0')} />
                        <span>{displayFields.starField.value}</span>
                        {displayFields.ratingsField && (
                          <span className="text-zinc-400 dark:text-zinc-500">
                            ({displayFields.ratingsField.value})
                          </span>
                        )}
                      </span>
                    )}
                    {displayFields?.usersField && (
                      <span className="flex items-center gap-1">
                        <svg
                          className="h-3.5 w-3.5 text-zinc-400"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          strokeWidth={1.5}
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z"
                          />
                        </svg>
                        {displayFields.usersField.value} readers
                      </span>
                    )}
                    {displayFields?.pagesField && (
                      <span>{displayFields.pagesField.value} pages</span>
                    )}
                    {displayFields?.lengthField && (
                      <span className="flex items-center gap-1">
                        <svg
                          className="h-3.5 w-3.5 text-zinc-400"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          strokeWidth={1.5}
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
                          />
                        </svg>
                        {displayFields.lengthField.value}
                      </span>
                    )}
                    {displayFields?.narratorField && (
                      <span className="flex items-center gap-1">
                        <svg
                          className="h-3.5 w-3.5 text-zinc-400"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          strokeWidth={1.5}
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M12 18.75a6 6 0 0 0 6-6v-1.5m-6 7.5a6 6 0 0 1-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 0 1-3-3V4.5a3 3 0 1 1 6 0v8.25a3 3 0 0 1-3 3Z"
                          />
                        </svg>
                        {displayFields.narratorField.value}
                      </span>
                    )}
                  </div>

                  {/* Series info */}
                  {book.series_name && (
                    <div className="flex items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400">
                      <span>
                        {book.series_position != null ? (
                          <>
                            #
                            {Number.isInteger(book.series_position)
                              ? book.series_position
                              : book.series_position}
                            {book.series_count ? ` of ${book.series_count}` : ''} in{' '}
                            {book.series_name}
                          </>
                        ) : (
                          <>Part of {book.series_name}</>
                        )}
                      </span>
                      {onSearchSeries && (
                        <button
                          type="button"
                          onClick={() => {
                            const seriesName = book.series_name;
                            if (!seriesName) {
                              return;
                            }
                            onSearchSeries(seriesName, book.series_id);
                            handleClose();
                          }}
                          className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-600 transition-colors hover:bg-emerald-100 dark:bg-emerald-900/20 dark:text-emerald-400 dark:hover:bg-emerald-900/40"
                        >
                          <svg
                            className="h-3 w-3"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                            strokeWidth={2}
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
                            />
                          </svg>
                          View series
                        </button>
                      )}
                    </div>
                  )}

                  {/* Description */}
                  {book.description && (
                    <div className="relative text-sm text-zinc-600 dark:text-zinc-400">
                      <p ref={descriptionRef} className={descriptionExpanded ? '' : 'line-clamp-3'}>
                        {book.description}
                        {descriptionExpanded && descriptionOverflows && (
                          <>
                            {' '}
                            <button
                              type="button"
                              onClick={() => setDescriptionExpanded(false)}
                              className="inline font-medium text-emerald-600 hover:underline dark:text-emerald-400"
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
                          className="absolute right-0 bottom-0 bg-linear-to-r from-transparent via-(--bg) to-(--bg) pl-8 font-medium text-emerald-600 hover:underline sm:via-(--bg-soft) sm:to-(--bg-soft) dark:text-emerald-400"
                        >
                          more
                        </button>
                      )}
                    </div>
                  )}

                  {/* Links row */}
                  <div className="mt-auto flex flex-wrap items-center gap-3 text-xs">
                    {(book.isbn_13 || book.isbn_10) && (
                      <span className="text-zinc-500 dark:text-zinc-400">
                        ISBN: {book.isbn_13 || book.isbn_10}
                      </span>
                    )}
                    {showBookSourceLink && (
                      <a
                        href={book.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-emerald-600 hover:underline dark:text-emerald-400"
                      >
                        View on {providerDisplay}
                        <svg
                          className="h-3 w-3"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                          />
                        </svg>
                      </a>
                    )}
                    {(onRequestBook || bookSupportsTargets(book)) && (
                      <span className="inline-flex items-center gap-3">
                        {onRequestBook && (
                          <button
                            type="button"
                            onClick={() => {
                              void handleRequestBook();
                            }}
                            disabled={isRequestingBook}
                            className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-600 transition-colors hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-emerald-900/20 dark:text-emerald-400 dark:hover:bg-emerald-900/40"
                          >
                            <svg
                              className="h-3 w-3"
                              fill="none"
                              stroke="currentColor"
                              viewBox="0 0 24 24"
                              strokeWidth={2}
                            >
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                d="M12 4.5v15m7.5-7.5h-15"
                              />
                            </svg>
                            {isRequestingBook ? 'Adding...' : 'Add to requests'}
                          </button>
                        )}
                        {bookSupportsTargets(book) && book.provider && book.provider_id && (
                          <BookTargetDropdown
                            provider={book.provider}
                            bookId={book.provider_id}
                            onShowToast={onShowToast}
                            variant="pill"
                          />
                        )}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Source tabs + filters - sticky within scroll container */}
            <div className="sticky top-0 z-10 border-b border-(--border-muted) bg-(--bg) sm:bg-(--bg-soft)">
              {sourcesLoading && (
                <div className="flex gap-1 px-5 py-2">
                  <div className="h-10 w-32 animate-pulse rounded-sm bg-zinc-200 dark:bg-zinc-700" />
                </div>
              )}
              {!sourcesLoading && allTabs.length === 0 && (
                <div className="px-5 py-3 text-sm text-zinc-500 dark:text-zinc-400">
                  {sourcesError || 'No release sources are available for this book.'}
                </div>
              )}
              {!sourcesLoading && allTabs.length > 0 && (
                <div className="flex items-center px-5">
                  {/* Tabs - scrollable on narrow screens */}
                  <div className="scrollbar-hide min-w-0 flex-1 overflow-x-auto">
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
                          type="button"
                          ref={(el) => {
                            tabRefs.current[tab.name] = el;
                          }}
                          onClick={() => setActiveTab(tab.name)}
                          className={`border-b-2 border-transparent px-4 py-2.5 text-sm font-medium whitespace-nowrap transition-colors ${
                            activeTab === tab.name
                              ? 'text-emerald-600 dark:text-emerald-400'
                              : 'text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-200'
                          }`}
                        >
                          {tab.displayName}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Results count from release sources. Deliberately not flex-1: a
                      second growing child would halve the scrollable tab strip beside
                      it, and it would claim that space even on tabs whose source
                      reports no total (only direct_download does). Hidden below sm:
                      the label is ~150px and the full-screen mobile modal has no room
                      for it without pushing the tabs out of reach. */}
                  <div className="hidden flex-none justify-center sm:flex">
                    {(() => {
                      const searchInfo = releasesBySource[activeTab]?.search_info?.[activeTab];
                      const totalCount = searchInfo?.total_results;
                      const filteredOut = searchInfo?.filtered_out_count;
                      if (totalCount === undefined || totalCount === null || totalCount === 0)
                        return null;
                      const isCapped = totalCount === '500+';
                      const totalCountNum = isCapped ? 500 : Number(totalCount);
                      // AA-style: show page range + total
                      if (totalCountNum === 1) {
                        const filterSuffix =
                          filteredOut && filteredOut > 0 ? `/${filteredOut} filtered` : '';
                        return (
                          <span className="mx-2 shrink-0 text-xs whitespace-nowrap text-gray-500 dark:text-gray-400">
                            Result 1 (1 Total{filterSuffix})
                          </span>
                        );
                      }
                      const shownEnd = Math.min(totalCountNum, filteredReleases.length);
                      const totalStr = isCapped ? '500+' : String(totalCountNum);
                      const filterSuffix =
                        filteredOut && filteredOut > 0 ? `/${filteredOut} filtered` : '';
                      return (
                        <span className="mx-2 shrink-0 text-xs whitespace-nowrap text-gray-500 dark:text-gray-400">
                          Results 1-{shownEnd} ({totalStr} Total{filterSuffix})
                        </span>
                      );
                    })()}
                  </div>

                  {/* Right: Controls */}
                  <div className="ml-auto flex items-center gap-3 pr-1 pl-2">
                    {/* Multi-book pack toggle (fallback for releases that can't be inspected) */}
                    {!isCombinedMode && (
                      <button
                        type="button"
                        onClick={() => setMultiBook((prev) => !prev)}
                        className={`hover-surface relative rounded-full p-2.5 text-zinc-500 transition-colors dark:text-zinc-400 ${
                          multiBook ? 'text-emerald-600 dark:text-emerald-400' : ''
                        }`}
                        aria-label="Multi-book pack"
                        aria-pressed={multiBook}
                        title="Multi-book pack: file each subfolder (or each file) as a separate book. Only needed when a release can't be inspected before download."
                      >
                        <svg
                          className="h-4 w-4"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          strokeWidth={1.5}
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M6.429 9.75 2.25 12l4.179 2.25m0-4.5 5.571 3 5.571-3m-11.142 0L2.25 7.5 12 2.25l9.75 5.25-4.179 2.25m0 0L21.75 12l-4.179 2.25m0 0 4.179 2.25L12 21.75 2.25 16.5l4.179-2.25m11.142 0-5.571 3-5.571-3"
                          />
                        </svg>
                        {multiBook && (
                          <span className="absolute top-1 right-1 h-2 w-2 rounded-full bg-emerald-500" />
                        )}
                      </button>
                    )}

                    {/* Manual query button */}
                    <button
                      type="button"
                      onClick={toggleManualQuery}
                      className={`hover-surface rounded-full p-2.5 text-zinc-500 transition-colors dark:text-zinc-400 ${
                        manualQuery.trim() ? 'text-emerald-600 dark:text-emerald-400' : ''
                      }`}
                      aria-label="Manual search query"
                      title="Manual query"
                    >
                      <svg
                        className="h-4 w-4"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                        strokeWidth={1.5}
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="m16.862 4.487 1.687-1.688a1.875 1.875 0 0 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0 1 15.75 21H5.25A2.25 2.25 0 0 1 3 18.75V8.25A2.25 2.25 0 0 1 5.25 6H10"
                        />
                      </svg>
                    </button>

                    {/* Sort dropdown - show if source has sort options or multiple formats */}
                    {(allSortOptions.length > 0 || availableFormats.length > 1) && (
                      <Dropdown
                        align="right"
                        widthClassName="w-auto shrink-0"
                        panelClassName="w-48"
                        renderTrigger={({ isOpen, toggle }) => (
                          <button
                            type="button"
                            onClick={toggle}
                            className={`hover-surface relative rounded-full p-2.5 text-zinc-500 transition-colors dark:text-zinc-400 ${
                              isOpen ? 'bg-(--hover-surface)' : ''
                            }`}
                            aria-label="Sort releases"
                          >
                            <svg
                              className="h-4 w-4"
                              fill="none"
                              stroke="currentColor"
                              viewBox="0 0 24 24"
                              strokeWidth={1.5}
                            >
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                d="M3 7.5 7.5 3m0 0L12 7.5M7.5 3v13.5m13.5 0L16.5 21m0 0L12 16.5m4.5 4.5V7.5"
                              />
                            </svg>
                            {currentSort && (
                              <span className="absolute top-1 right-1 h-2 w-2 rounded-full bg-emerald-500" />
                            )}
                          </button>
                        )}
                      >
                        {({ close }) => (
                          <div className="py-1">
                            {/* Default option - book-title best match */}
                            <button
                              type="button"
                              onClick={() => {
                                handleSortChange(null, 'asc');
                                setFormatSortExpanded(false);
                                close();
                              }}
                              className={`hover-surface flex w-full items-center justify-between rounded px-3 py-2 text-left text-sm ${
                                !currentSort
                                  ? 'font-medium text-emerald-600 dark:text-emerald-400'
                                  : 'text-zinc-700 dark:text-zinc-300'
                              }`}
                            >
                              <span>Best Match (Default)</span>
                              {!currentSort && (
                                <svg
                                  className="h-4 w-4"
                                  fill="none"
                                  stroke="currentColor"
                                  viewBox="0 0 24 24"
                                  strokeWidth={2}
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    d="m4.5 12.75 6 6 9-13.5"
                                  />
                                </svg>
                              )}
                            </button>
                            {allSortOptions.map((opt) => {
                              const isSelected = currentSort?.key === opt.sortKey;
                              const direction = isSelected ? currentSort?.direction : null;
                              return (
                                <button
                                  key={opt.sortKey}
                                  type="button"
                                  onClick={() => {
                                    handleSortChange(opt.sortKey, opt.defaultDirection);
                                    setFormatSortExpanded(false);
                                    // Don't close - allow toggling direction
                                    if (!isSelected) close();
                                  }}
                                  className={`hover-surface flex w-full items-center justify-between rounded px-3 py-2 text-left text-sm ${
                                    isSelected
                                      ? 'font-medium text-emerald-600 dark:text-emerald-400'
                                      : 'text-zinc-700 dark:text-zinc-300'
                                  }`}
                                >
                                  <span>{opt.label}</span>
                                  {isSelected && direction && (
                                    <svg
                                      className="h-4 w-4"
                                      fill="none"
                                      stroke="currentColor"
                                      viewBox="0 0 24 24"
                                      strokeWidth={2}
                                    >
                                      {direction === 'asc' ? (
                                        <path
                                          strokeLinecap="round"
                                          strokeLinejoin="round"
                                          d="M4.5 15.75l7.5-7.5 7.5 7.5"
                                        />
                                      ) : (
                                        <path
                                          strokeLinecap="round"
                                          strokeLinejoin="round"
                                          d="M19.5 8.25l-7.5 7.5-7.5-7.5"
                                        />
                                      )}
                                    </svg>
                                  )}
                                </button>
                              );
                            })}

                            {/* Format priority sort sub-menu */}
                            {availableFormats.length > 1 && (
                              <>
                                {allSortOptions.length > 0 && (
                                  <div className="mx-2 my-1 border-t border-zinc-200 dark:border-zinc-700" />
                                )}
                                <button
                                  type="button"
                                  onClick={() => setFormatSortExpanded((prev) => !prev)}
                                  className={`hover-surface flex w-full items-center justify-between rounded px-3 py-2 text-left text-sm ${
                                    currentSort?.key === FORMAT_SORT_KEY
                                      ? 'font-medium text-emerald-600 dark:text-emerald-400'
                                      : 'text-zinc-700 dark:text-zinc-300'
                                  }`}
                                >
                                  <span>
                                    Format
                                    {currentSort?.key === FORMAT_SORT_KEY && currentSort.value
                                      ? ` (${currentSort.value.toUpperCase()})`
                                      : ''}
                                  </span>
                                  <svg
                                    className={`h-4 w-4 transition-transform ${formatSortExpanded ? 'rotate-90' : ''}`}
                                    fill="none"
                                    stroke="currentColor"
                                    viewBox="0 0 24 24"
                                    strokeWidth={2}
                                  >
                                    <path
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                      d="M8.25 4.5l7.5 7.5-7.5 7.5"
                                    />
                                  </svg>
                                </button>
                                {formatSortExpanded &&
                                  availableFormats.map((fmt) => {
                                    const isSelected =
                                      currentSort?.key === FORMAT_SORT_KEY &&
                                      currentSort.value === fmt;
                                    const direction = isSelected ? currentSort?.direction : null;
                                    return (
                                      <button
                                        key={fmt}
                                        type="button"
                                        onClick={() => {
                                          handleSortChange(FORMAT_SORT_KEY, 'asc', fmt);
                                          if (!isSelected) close();
                                        }}
                                        className={`hover-surface flex w-full items-center justify-between rounded py-1.5 pr-3 pl-6 text-left text-sm ${
                                          isSelected
                                            ? 'font-medium text-emerald-600 dark:text-emerald-400'
                                            : 'text-zinc-700 dark:text-zinc-300'
                                        }`}
                                      >
                                        <span>{fmt.toUpperCase()}</span>
                                        {isSelected && direction && (
                                          <svg
                                            className="h-4 w-4"
                                            fill="none"
                                            stroke="currentColor"
                                            viewBox="0 0 24 24"
                                            strokeWidth={2}
                                          >
                                            {direction === 'asc' ? (
                                              <path
                                                strokeLinecap="round"
                                                strokeLinejoin="round"
                                                d="M4.5 15.75l7.5-7.5 7.5 7.5"
                                              />
                                            ) : (
                                              <path
                                                strokeLinecap="round"
                                                strokeLinejoin="round"
                                                d="M19.5 8.25l-7.5 7.5-7.5-7.5"
                                              />
                                            )}
                                          </svg>
                                        )}
                                      </button>
                                    );
                                  })}
                              </>
                            )}
                          </div>
                        )}
                      </Dropdown>
                    )}

                    {/* Filter funnel button - stays fixed */}
                    {/* Only show filter button if source supports at least one filter type */}
                    {((columnConfig.supported_filters?.includes('format') &&
                      availableFormats.length > 0) ||
                      (columnConfig.supported_filters?.includes('language') &&
                        bookLanguages.length > 0) ||
                      (columnConfig.supported_filters?.includes('indexer') &&
                        availableIndexers.length > 1)) && (
                      <Dropdown
                        align="right"
                        widthClassName="w-auto shrink-0"
                        panelClassName="w-56"
                        noScrollLimit
                        renderTrigger={({ isOpen, toggle }) => {
                          // Active filter: format is set, language is not default, or indexers differ from defaults
                          const hasLanguageFilter = !(
                            languageFilter.length === 1 &&
                            languageFilter[0] === LANGUAGE_OPTION_DEFAULT
                          );
                          const supportsIndexerFilter =
                            columnConfig.supported_filters?.includes('indexer');
                          // Check if indexer filter differs from defaults (only after initialization)
                          // Don't show dot while loading or before filter is initialized from defaults
                          const hasResults = releasesBySource[activeTab]?.releases !== undefined;
                          const isInitialized = isIndexerFilterInitialized(activeTab);
                          const defaultIndexers = columnConfig.default_indexers ?? [];
                          const indexersMatchDefault =
                            indexerFilter.length === defaultIndexers.length &&
                            indexerFilter.every((idx) => defaultIndexers.includes(idx));
                          const hasIndexerFilter =
                            supportsIndexerFilter &&
                            hasResults &&
                            isInitialized &&
                            !indexersMatchDefault;
                          const hasActiveFilter =
                            formatFilter !== '' || hasLanguageFilter || hasIndexerFilter;
                          return (
                            <button
                              type="button"
                              onClick={toggle}
                              className={`hover-surface relative rounded-full p-2.5 text-zinc-500 transition-colors dark:text-zinc-400 ${
                                isOpen ? 'bg-(--hover-surface)' : ''
                              }`}
                              aria-label="Filter releases"
                            >
                              <svg
                                className="h-4 w-4"
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                                strokeWidth={1.5}
                              >
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 0 1-.659 1.591l-5.432 5.432a2.25 2.25 0 0 0-.659 1.591v2.927a2.25 2.25 0 0 1-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 0 0-.659-1.591L3.659 7.409A2.25 2.25 0 0 1 3 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0 1 12 3Z"
                                />
                              </svg>
                              {hasActiveFilter && (
                                <span className="absolute top-1 right-1 h-2 w-2 rounded-full bg-emerald-500" />
                              )}
                            </button>
                          );
                        }}
                      >
                        {({ close }) => (
                          <div className="space-y-4 p-4">
                            {columnConfig.supported_filters?.includes('format') &&
                              availableFormats.length > 0 && (
                                <DropdownList
                                  label="Format"
                                  options={formatOptions}
                                  value={formatFilter}
                                  onChange={(val) =>
                                    setFormatFilter(typeof val === 'string' ? val : (val[0] ?? ''))
                                  }
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
                            {columnConfig.supported_filters?.includes('indexer') &&
                              availableIndexers.length > 1 && (
                                <DropdownList
                                  label="Indexers"
                                  options={availableIndexers.map((idx) => ({
                                    value: idx,
                                    label: idx,
                                  }))}
                                  multiple
                                  value={indexerFilter}
                                  onChange={(val) => {
                                    let nextIndexerFilter: string[] = [];
                                    if (Array.isArray(val)) {
                                      nextIndexerFilter = val;
                                    } else if (val) {
                                      nextIndexerFilter = [val];
                                    }
                                    setIndexerFilter(nextIndexerFilter);
                                  }}
                                  placeholder="All Indexers"
                                />
                              )}
                            {/* Apply button - re-fetch when the source supports server-side filters */}
                            {(columnConfig.supported_filters?.includes('language') ||
                              columnConfig.supported_filters?.includes('indexer')) && (
                              <button
                                type="button"
                                onClick={() => {
                                  close();
                                  applyCurrentFilters();
                                }}
                                className="w-full rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-700"
                              >
                                Apply
                              </button>
                            )}
                          </div>
                        )}
                      </Dropdown>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Manual query panel (below source tabs) */}
            {showManualQuery && (
              <div className="border-b border-(--border-muted) bg-(--bg) px-5 py-3 sm:bg-(--bg-soft)">
                <form
                  className="flex items-center gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    runManualSearch();
                  }}
                >
                  <input
                    type="text"
                    value={manualQuery}
                    onChange={(e) => setManualQuery(e.target.value)}
                    placeholder="Type a custom search query (overrides all sources)"
                    aria-label="Custom search query"
                    className="w-full rounded-lg border border-(--border-muted) bg-(--bg) px-3 py-2 text-sm text-(--text)"
                  />
                  <button
                    type="submit"
                    disabled={currentTabLoading || !manualQuery.trim()}
                    className={`rounded-lg px-3 py-2 text-sm font-medium text-white transition-colors ${
                      currentTabLoading || !manualQuery.trim()
                        ? 'cursor-not-allowed bg-emerald-600/60'
                        : 'bg-emerald-600 hover:bg-emerald-700'
                    }`}
                  >
                    {currentTabLoading ? 'Searching…' : 'Search'}
                  </button>
                </form>
                <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-400">
                  Manual query overrides ISBN/title/author/language expansion.
                </p>
              </div>
            )}

            {/* Release list content */}
            <div className="min-h-[200px]">
              {(() => {
                if (packReview) {
                  return (
                    <PackReviewPanel
                      release={packReview.release}
                      plan={packReview.plan}
                      books={packReview.books}
                      onChange={(books) => setPackReview({ ...packReview, books })}
                      onBack={() => setPackReview(null)}
                      onConfirm={handlePackConfirm}
                      isSubmitting={packSubmitting}
                    />
                  );
                }
                if (sourcesLoading) {
                  return <ReleaseSkeleton />;
                }
                if (sourcesError) {
                  return <ErrorState message={sourcesError} />;
                }
                if (!hasActiveTab) {
                  return <EmptyState message="No release sources are available for this book." />;
                }
                if (isInitialLoading && filteredReleases.length === 0) {
                  return <ReleaseSkeleton />;
                }
                if (currentTabError) {
                  return <ErrorState message={currentTabError} />;
                }
                if (filteredReleases.length === 0 && !currentTabLoading) {
                  return (
                    <>
                      <EmptyState message={emptyStateMessage} />
                      {/* Action button - plugin-defined or default expand search */}
                      {(columnConfig.action_button ||
                        (!expandedBySource[activeTab] &&
                          releasesBySource[activeTab]?.search_info?.[activeTab]?.search_type &&
                          !['title_author', 'expanded'].includes(
                            releasesBySource[activeTab]?.search_info?.[activeTab]?.search_type ??
                              '',
                          ))) && (
                        <div className="py-3 text-center">
                          <button
                            type="button"
                            onClick={() => {
                              void expandSearch();
                            }}
                            className="hover-action rounded-full px-3 py-1.5 text-sm text-zinc-500 transition-all duration-200 dark:text-zinc-400"
                          >
                            {columnConfig.action_button?.label ?? 'Expand search'}
                          </button>
                        </div>
                      )}
                    </>
                  );
                }

                return (
                  <>
                    {/* Key includes filter to force remount when filter changes */}
                    <div
                      key={`releases-${formatFilter}-${languageFilter.join(',')}`}
                      className="divide-y divide-zinc-200/60 dark:divide-zinc-800/60"
                    >
                      {filteredReleases.map((release, index) => (
                        <ReleaseRow
                          key={`${release.source}-${release.source_id}`}
                          release={release}
                          index={index}
                          onDownload={() => handleReleaseAction(release)}
                          buttonState={getButtonState(release)}
                          columns={columnConfig.columns}
                          gridTemplate={columnConfig.grid_template}
                          leadingCell={columnConfig.leading_cell}
                          onlineServers={columnConfig.online_servers}
                          showReleaseSourceLinks={showReleaseSourceLinks}
                          selectionMode={isCombinedMode}
                          isSelected={
                            isCombinedMode && selectedRelease?.source_id === release.source_id
                          }
                          onSelect={isCombinedMode ? () => setSelectedRelease(release) : undefined}
                        />
                      ))}
                    </div>
                    {/* Action button - plugin-defined or default expand search */}
                    {!currentTabLoading &&
                      (columnConfig.action_button ||
                        (!expandedBySource[activeTab] &&
                          releasesBySource[activeTab]?.search_info?.[activeTab]?.search_type &&
                          !['title_author', 'expanded'].includes(
                            releasesBySource[activeTab]?.search_info?.[activeTab]?.search_type ??
                              '',
                          ))) && (
                        <div
                          className="animate-pop-up py-3 text-center"
                          style={{
                            animationDelay: `${filteredReleases.length * 30}ms`,
                            animationFillMode: 'both',
                          }}
                        >
                          <button
                            type="button"
                            onClick={() => {
                              void expandSearch();
                            }}
                            className="hover-action rounded-full px-3 py-1.5 text-sm text-zinc-500 transition-all duration-200 dark:text-zinc-400"
                          >
                            {columnConfig.action_button?.label ?? 'Expand search'}
                          </button>
                        </div>
                      )}
                    {/* Expanding search - show skeleton below existing results */}
                    {currentTabLoading && filteredReleases.length > 0 && <ReleaseSkeleton />}
                  </>
                );
              })()}
            </div>

            {/* Sticky search status indicator - stays at bottom of visible scroll area */}
            {searchStatus && searchStatus.source === activeTab && currentTabLoading && (
              <div className="pointer-events-none sticky bottom-0 z-10 flex items-center justify-center pt-2 pb-4">
                <div className="pointer-events-auto flex items-center gap-2.5 rounded-xl border border-(--border-muted) bg-(--bg-soft) px-4 py-2 text-sm text-zinc-500 shadow-lg dark:text-zinc-400">
                  {searchStatus.phase !== 'complete' && searchStatus.phase !== 'error' && (
                    <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  )}
                  {searchStatus.message}
                </div>
              </div>
            )}
          </div>

          {/* Combined mode footer */}
          {isCombinedMode && (
            <div className="border-t border-(--border-muted) bg-(--bg) px-5 py-4 sm:bg-(--bg-soft)">
              <div className="flex flex-wrap items-center justify-between gap-2 sm:gap-3">
                {/* Phase indicators with live selection chips */}
                <div className="flex min-w-0 flex-col items-start gap-1 text-sm sm:flex-row sm:items-center sm:gap-3">
                  <PhaseChip
                    release={combinedPhase === 'ebook' ? selectedRelease : stagedEbookRelease}
                    isActive={combinedPhase === 'ebook'}
                    label="Book"
                    onClear={clearEbookSelection}
                  />
                  <PhaseChip
                    release={
                      combinedPhase === 'audiobook' ? selectedRelease : stagedAudiobookRelease
                    }
                    isActive={combinedPhase === 'audiobook'}
                    label="Audiobook"
                    onClear={clearAudiobookSelection}
                  />
                </div>

                {/* Action buttons */}
                <div className="flex shrink-0 items-center justify-end gap-3">
                  {onCombinedBack && (
                    <button
                      type="button"
                      onClick={() => {
                        const picked = selectedRelease;
                        setSelectedRelease(stagedEbookRelease);
                        onCombinedBack(picked);
                      }}
                      className="hover-surface rounded-lg px-3 py-1.5 text-sm font-medium text-(--text) transition-colors"
                    >
                      &larr; Back
                    </button>
                  )}

                  {combinedPhase === 'ebook' && onCombinedNext && (
                    <button
                      type="button"
                      onClick={() => {
                        if (selectedRelease) {
                          const picked = selectedRelease;
                          setSelectedRelease(stagedAudiobookRelease);
                          onCombinedNext(picked);
                        } else {
                          setSelectedRelease(stagedAudiobookRelease);
                          onCombinedNext(null);
                        }
                      }}
                      className="rounded-lg bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {selectedRelease
                        ? `Select ${nextCombinedPhaseLabel} →`
                        : `Skip ${currentCombinedPhaseLabel} →`}
                    </button>
                  )}

                  {onCombinedDownload && (
                    <button
                      type="button"
                      onClick={() => {
                        if (selectedRelease) {
                          onCombinedDownload(selectedRelease);
                        } else if (canCompleteCombinedAction) {
                          onCombinedDownload(null);
                        }
                      }}
                      disabled={!canCompleteCombinedAction}
                      className="rounded-lg bg-emerald-600 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {getCombinedDownloadLabel(
                        combinedFooterEbookMode,
                        combinedFooterAudiobookMode,
                        hasCombinedEbookAction,
                        hasCombinedAudiobookAction,
                      )}
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );

  if (typeof document === 'undefined') {
    return modal;
  }

  return createPortal(modal, document.body);
};

export const ReleaseModal = ({ book, onClose, ...rest }: ReleaseModalProps) => {
  const [isClosing, setIsClosing] = useState(false);
  const [enterAnimation, setEnterAnimation] = useState(INITIAL_ENTER_ANIMATION);

  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
    }, 150);
  }, [onClose]);

  useBodyScrollLock(Boolean(book));
  useEscapeKey(Boolean(book), handleClose);

  const sessionKey = book
    ? [
        book.id,
        rest.defaultShowManualQuery ? 'manual' : 'auto',
        book.search_title || '',
        book.title || '',
        book.search_author || '',
        book.author || '',
      ].join('|')
    : null;

  // Decided once per session key and held for that session's lifetime, so a
  // re-render mid-session cannot restart the enter animation.
  const nextAnimation = nextEnterAnimation(enterAnimation, sessionKey, rest.combinedMode != null);
  if (nextAnimation !== enterAnimation) {
    setEnterAnimation(nextAnimation);
  }
  const animateEnter = nextAnimation.animate;

  if (!book && !isClosing) return null;
  if (!book || !sessionKey) return null;

  return (
    <ReleaseModalSession
      key={sessionKey}
      book={book}
      onClose={handleClose}
      isClosing={isClosing}
      animateEnter={animateEnter}
      {...rest}
    />
  );
};

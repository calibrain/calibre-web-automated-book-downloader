import { t } from '../i18n';
import type { ColumnSchema, Release } from '../types';
import { getColorStyleFromHint, getProtocolDotColor, getFormatColor } from '../utils/colorMaps';
import {
  getNestedValue,
  isRecord,
  toComparableText,
  toNumberValue,
  toStringArray,
  toStringValue,
} from '../utils/objectHelpers';
import { getUnrecognizedReleaseFormats } from '../utils/releaseFormats';
import { Tooltip } from './shared/Tooltip';

interface ReleaseCellProps {
  column: ColumnSchema;
  release: Release;
  compact?: boolean; // When true, renders badges as plain text (for mobile info lines)
  onlineServers?: string[]; // For IRC: list of online server nicks to show status indicator
}

const normalizeFlagLabel = (tag: string): string => {
  const normalized = tag.trim().toLowerCase();
  if (!normalized) return tag;

  switch (normalized) {
    case 'freeleech':
    case 'free leech':
    case 'fl':
      return 'FL';
    case 'double upload':
    case 'doubleupload':
    case 'du':
      return 'DU';
    case 'vip':
      return 'VIP';
    case 'internal':
    case 'int':
      return 'INT';
    default:
      return tag;
  }
};

const formatRelativeTime = (dateString: string): string | null => {
  try {
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return null;

    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return t('Today');
    if (diffDays === 1) return t('1 day');

    return t('{count} days', { count: diffDays });
  } catch {
    return null;
  }
};

const getDuplicateAwareKey = (counts: Map<string, number>, keyBase: string): string => {
  const nextCount = (counts.get(keyBase) ?? 0) + 1;
  counts.set(keyBase, nextCount);

  return nextCount === 1 ? keyBase : `${keyBase}-${nextCount}`;
};

/**
 * Generic cell renderer for release list columns.
 * Renders different column types (text, badge, size, number, seeders) based on schema.
 * When compact=true, badges render as plain text for use in mobile info lines.
 */
export const ReleaseCell = ({
  column,
  release,
  compact = false,
  onlineServers,
}: ReleaseCellProps) => {
  const rawValue = getNestedValue(release, column.key);
  const cellValue =
    rawValue !== undefined && rawValue !== null ? toComparableText(rawValue) : column.fallback;

  const displayValue = column.uppercase ? cellValue.toUpperCase() : cellValue;

  // Alignment classes
  const alignClass = {
    left: 'text-left justify-start',
    center: 'text-center justify-center',
    right: 'text-right justify-end',
  }[column.align];

  // Render based on type
  switch (column.render_type) {
    case 'badge': {
      // Compact mode: render as plain text (for mobile info lines)
      if (compact) {
        return <span>{displayValue}</span>;
      }
      const colorStyle = getColorStyleFromHint(cellValue, column.color_hint);

      // Build rich tooltip content for formats
      let tooltipContent: React.ReactNode = null;
      if (column.key === 'extra.formats_display') {
        const formats = release.extra?.formats;
        if (Array.isArray(formats) && formats.length > 1) {
          tooltipContent = (
            <div className="flex flex-wrap gap-1.5">
              {formats.map((fmt) => {
                const fmtColor = getFormatColor(String(fmt));
                return (
                  <span
                    key={String(fmt)}
                    className={`${fmtColor.bg} ${fmtColor.text} rounded-lg px-1.5 py-0.5 text-[10px] font-semibold tracking-wide sm:px-2 sm:text-[11px]`}
                  >
                    {String(fmt).toUpperCase()}
                  </span>
                );
              })}
            </div>
          );
        }
      }

      const badge = (
        <span
          className={`${colorStyle.bg} ${colorStyle.text} rounded-lg px-1.5 py-0.5 text-[10px] font-semibold tracking-wide sm:px-2 sm:text-[11px]`}
        >
          {displayValue}
        </span>
      );

      return (
        <div className={`flex items-center ${alignClass}`}>
          {cellValue !== column.fallback ? (
            tooltipContent ? (
              <Tooltip content={tooltipContent} position="top">
                {badge}
              </Tooltip>
            ) : (
              badge
            )
          ) : (
            <span className="text-[10px] text-gray-500 sm:text-xs dark:text-gray-400">
              {t(column.fallback)}
            </span>
          )}
        </div>
      );
    }

    case 'tags': {
      let tags: string[] = [];
      if (Array.isArray(rawValue)) {
        tags = rawValue.map((tag) => toComparableText(tag)).filter((tag) => tag.trim());
      } else if (rawValue !== undefined && rawValue !== null) {
        const comparableValue = toComparableText(rawValue).trim();
        if (comparableValue) {
          tags = [comparableValue];
        }
      }
      const isFlags = column.color_hint?.type === 'map' && column.color_hint.value === 'flags';

      if (compact) {
        if (tags.length === 0) {
          return column.fallback ? <span>{t(column.fallback)}</span> : null;
        }
        const displayTags = tags.map((tag) => {
          const normalized = isFlags ? normalizeFlagLabel(tag) : tag;
          return column.uppercase ? normalized.toUpperCase() : normalized;
        });
        return <span>{displayTags.join(', ')}</span>;
      }

      if (!tags.length) {
        if (!column.fallback) {
          return <div className={`flex items-center ${alignClass}`} />;
        }
        return (
          <div className={`flex items-center ${alignClass}`}>
            <span className="text-[10px] text-gray-500 sm:text-xs dark:text-gray-400">
              {t(column.fallback)}
            </span>
          </div>
        );
      }

      const tagKeyCounts = new Map<string, number>();

      return (
        <div className={`flex flex-wrap items-center gap-1.5 ${alignClass}`}>
          {tags.map((tag) => {
            const normalized = isFlags ? normalizeFlagLabel(tag) : tag;
            const displayTag = column.uppercase ? normalized.toUpperCase() : normalized;
            const colorStyle = getColorStyleFromHint(tag, column.color_hint);
            const tagKey = getDuplicateAwareKey(tagKeyCounts, `${column.key}|${displayTag}`);

            return (
              <span
                key={tagKey}
                className={`${colorStyle.bg} ${colorStyle.text} rounded-lg px-1.5 py-0.5 text-[10px] font-semibold tracking-wide whitespace-nowrap sm:px-2 sm:text-[11px]`}
              >
                {displayTag}
              </span>
            );
          })}
        </div>
      );
    }

    case 'size': {
      // Build tooltip from extra metadata (torznab attrs, publish date, etc.)
      const extra = release.extra;
      const torznabAttrs = isRecord(extra?.torznab_attrs) ? extra.torznab_attrs : undefined;
      const publishDate = toStringValue(extra?.publish_date);
      const postedDate = toStringValue(extra?.posted_date);
      const bitrate = toStringValue(extra?.bitrate);

      const rows: Array<{ label: string; value: string }> = [];

      // Add publish date first if available
      if (publishDate) {
        const relativeTime = formatRelativeTime(publishDate);
        if (relativeTime) {
          rows.push({ label: t('Added'), value: relativeTime });
        }
      }

      if (postedDate) {
        const postedDateValue = postedDate.trim();
        const relativeTime = formatRelativeTime(postedDateValue);
        rows.push({ label: t('Posted'), value: relativeTime ?? postedDateValue });
      }

      if (bitrate) {
        const bitrateValue = bitrate.trim();
        if (bitrateValue) {
          rows.push({ label: t('Bitrate'), value: bitrateValue });
        }
      }

      // Add torznab attributes if available (MAM, etc.)
      if (torznabAttrs && Object.keys(torznabAttrs).length > 0) {
        const displayAttrs: Array<{ key: string; label: string }> = [
          { key: 'description', label: t('Description') },
          { key: 'year', label: t('Year') },
          { key: 'genre', label: t('Genre') },
          { key: 'narrator', label: t('Narrator') },
          { key: 'bitrate', label: t('Bitrate') },
          { key: 'samplerate', label: t('Sample Rate') },
          { key: 'runtime', label: t('Runtime') },
          { key: 'pages', label: t('Pages') },
          { key: 'publisher', label: t('Publisher') },
          { key: 'language', label: t('Language') },
        ];

        for (const attr of displayAttrs) {
          // Already listed above from extra.bitrate (Prowlarr copies the attribute there).
          if (attr.key === 'bitrate' && bitrate) continue;
          const val = toStringValue(torznabAttrs[attr.key]);
          if (val && val.trim()) {
            rows.push({ label: attr.label, value: val.trim() });
          }
        }
      }

      // Add files and grabs from extra
      const files = toNumberValue(extra?.files);
      const grabs = toNumberValue(extra?.grabs);
      if (files !== undefined && files !== null) {
        rows.push({ label: t('Files'), value: String(files) });
      }
      if (grabs !== undefined && grabs !== null) {
        rows.push({ label: t('Grabs'), value: String(grabs) });
      }

      let sizeTooltipContent: React.ReactNode = null;
      if (rows.length > 0) {
        sizeTooltipContent = (
          <div className="flex max-w-xs flex-col gap-1">
            {rows.map((row) => (
              <div key={row.label} className="flex gap-2">
                <span className="shrink-0 text-gray-400 dark:text-gray-500">{row.label}:</span>
                <span className="truncate">{row.value}</span>
              </div>
            ))}
          </div>
        );
      }

      if (compact) {
        return <span>{displayValue}</span>;
      }

      const sizeText = <span>{displayValue}</span>;

      return (
        <div className={`flex items-center ${alignClass} text-xs text-gray-600 dark:text-gray-300`}>
          {sizeTooltipContent ? (
            <Tooltip content={sizeTooltipContent} position="left">
              {sizeText}
            </Tooltip>
          ) : (
            sizeText
          )}
        </div>
      );
    }

    case 'peers': {
      // Peers display: "S/L" string with badge colored by seeder count
      // Color logic: 0 = red, 1-10 = yellow, 10+ = blue
      const seeders = release.seeders;
      const peersValue = cellValue || column.fallback;
      const isFallback = seeders == null || peersValue === column.fallback;

      // If no data, show plain text like badge type does
      if (isFallback) {
        if (compact) {
          return <span>{t(column.fallback)}</span>;
        }
        return (
          <div className={`flex items-center ${alignClass}`}>
            <span className="text-[10px] text-gray-500 sm:text-xs dark:text-gray-400">
              {t(column.fallback)}
            </span>
          </div>
        );
      }

      // Determine color based on seeder count
      let badgeColors: string;
      if (seeders >= 10) {
        badgeColors = 'bg-blue-500/20 text-blue-700 dark:text-blue-300';
      } else if (seeders >= 1) {
        badgeColors = 'bg-yellow-500/20 text-yellow-700 dark:text-yellow-300';
      } else {
        badgeColors = 'bg-red-500/20 text-red-700 dark:text-red-300';
      }

      if (compact) {
        return (
          <span className={`font-medium ${badgeColors.split(' ').slice(1).join(' ')}`}>
            {peersValue}
          </span>
        );
      }
      return (
        <div className={`flex items-center ${alignClass}`}>
          <span
            className={`${badgeColors} rounded-lg px-1.5 py-0.5 text-[10px] font-semibold tracking-wide sm:px-2 sm:text-[11px]`}
          >
            {peersValue}
          </span>
        </div>
      );
    }

    case 'indexer_protocol': {
      // Indexer name with colored dot indicating protocol (torrent/usenet) and peers count
      const protocol = release.protocol as string | undefined;
      const dotColor = getProtocolDotColor(protocol);
      let protocolLabel = protocol || t('Unknown');
      if (protocol === 'torrent') {
        protocolLabel = 'Torrent';
      } else if (protocol === 'nzb') {
        protocolLabel = 'Usenet';
      }
      const peers = release.peers;

      if (compact) {
        return (
          <span className="inline-flex items-center gap-1">
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotColor}`}
              title={protocolLabel}
            />
            {displayValue}
            {peers && <span className="text-gray-400">({peers})</span>}
          </span>
        );
      }
      return (
        <div
          className={`flex items-center ${alignClass} gap-1.5 truncate text-xs text-gray-600 dark:text-gray-300`}
        >
          <span className={`h-2 w-2 shrink-0 rounded-full ${dotColor}`} title={protocolLabel} />
          {/* Titled because one tracker can appear as several indexer entries whose
              names share a prefix, and truncation would make the rows look identical */}
          <span className="truncate" title={displayValue}>
            {displayValue}
          </span>
          {peers && <span className="shrink-0 text-gray-400 dark:text-gray-500">{peers}</span>}
        </div>
      );
    }

    case 'flag_icon': {
      // Colored badge showing FL, VIP, or both
      if (!cellValue || cellValue === column.fallback) {
        if (compact) return null;
        return <div className={`flex items-center ${alignClass}`} />;
      }

      const flagColor = getColorStyleFromHint(cellValue, { type: 'map', value: 'flags' });

      if (compact) {
        return <span className={`${flagColor.text} font-medium`}>{cellValue}</span>;
      }
      return (
        <div className={`flex items-center ${alignClass}`}>
          <span
            className={`${flagColor.bg} ${flagColor.text} rounded-lg px-1.5 py-0.5 text-[10px] font-semibold tracking-wide whitespace-nowrap sm:px-2 sm:text-[11px]`}
          >
            {cellValue}
          </span>
        </div>
      );
    }

    case 'format_content_type': {
      // Content type icon + format badge combined
      // Shows primary format as badge with colored dots for additional formats
      const contentType = release.content_type;
      const isAudiobook = contentType === 'audiobook';
      const formats = toStringArray(release.extra?.formats);
      const primaryFormat = formats?.[0] || null;
      const additionalFormats = formats?.slice(1) || [];

      // The indexer named a format Shelfmark can't process (e.g. MAM "[ENG / AVI]").
      // Downloading it would only fail post-processing, so warn instead of showing the
      // bare content-type icon that makes it look like any other result.
      const unrecognizedFormats = primaryFormat ? [] : getUnrecognizedReleaseFormats(release);
      if (unrecognizedFormats.length > 0) {
        const unsupportedLabel = unrecognizedFormats.map((fmt) => fmt.toUpperCase()).join(', ');
        const unsupportedTitle = t(
          'Unsupported format ({formats}) - Shelfmark cannot process this release',
          { formats: unsupportedLabel },
        );
        if (compact) {
          return (
            <span
              className="font-semibold text-amber-600 dark:text-amber-400"
              title={unsupportedTitle}
            >
              {unrecognizedFormats[0].toUpperCase()}
              {unrecognizedFormats.length > 1 && ` +${unrecognizedFormats.length - 1}`}
            </span>
          );
        }
        return (
          <div className="flex items-center justify-start" title={unsupportedTitle}>
            <span className="inline-flex items-center gap-1">
              <span className="w-13 rounded-lg bg-amber-500/20 py-0.5 text-center text-[10px] font-semibold tracking-wide whitespace-nowrap text-amber-700 sm:text-[11px] dark:text-amber-400">
                {unrecognizedFormats[0].toUpperCase()}
              </span>
              <span className="text-[10px] font-medium whitespace-nowrap text-amber-700 sm:text-[11px] dark:text-amber-400">
                {t('Unsupported')}
              </span>
            </span>
          </div>
        );
      }

      // Use blue for book, violet for audiobook when no format specified
      const noFormatStyle = isAudiobook
        ? { bg: 'bg-violet-500/20', text: 'text-violet-600 dark:text-violet-400' }
        : { bg: 'bg-blue-500/20', text: 'text-blue-600 dark:text-blue-400' };
      const colorStyle = primaryFormat ? getFormatColor(primaryFormat) : noFormatStyle;

      // Build rich tooltip content for formats (if multiple)
      let tooltipContent: React.ReactNode = null;
      if (formats && formats.length > 1) {
        tooltipContent = (
          <div className="flex flex-wrap gap-1.5">
            {formats.map((fmt) => {
              const fmtColor = getFormatColor(fmt);
              return (
                <span
                  key={fmt}
                  className={`${fmtColor.bg} ${fmtColor.text} rounded-lg px-1.5 py-0.5 text-[10px] font-semibold tracking-wide sm:px-2 sm:text-[11px]`}
                >
                  {fmt.toUpperCase()}
                </span>
              );
            })}
          </div>
        );
      }

      // Icon sized to match visual height of format text badges
      const icon = isAudiobook ? (
        // Headphones icon for audiobook
        <svg
          className="h-4 w-4 shrink-0"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          strokeWidth={2}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z"
          />
        </svg>
      ) : (
        // Book icon for ebook
        <svg
          className="h-4 w-4 shrink-0"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          strokeWidth={2}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"
          />
        </svg>
      );

      if (compact) {
        if (!primaryFormat) {
          return (
            <span
              className="inline-flex items-center text-gray-500"
              title={isAudiobook ? t('Audiobook') : t('Book')}
            >
              {icon}
            </span>
          );
        }
        // Simple text tooltip for compact mode
        const compactTooltip =
          formats && formats.length > 1
            ? formats.map((fmt) => fmt.toUpperCase()).join(', ')
            : undefined;
        return (
          <span className={column.uppercase ? 'uppercase' : ''} title={compactTooltip}>
            {primaryFormat.toUpperCase()}
            {additionalFormats.length > 0 && ` +${additionalFormats.length}`}
          </span>
        );
      }

      // No format - just show icon with same width as format badges
      if (!primaryFormat) {
        return (
          <div
            className="flex items-center justify-start"
            title={isAudiobook ? t('Audiobook') : t('Book')}
          >
            <span
              className={`${colorStyle.bg} ${colorStyle.text} inline-flex w-13 items-center justify-center rounded-lg py-0.5 text-[10px] font-semibold sm:text-[11px]`}
            >
              {icon}
            </span>
          </div>
        );
      }

      // Format badge - left-aligned so primary format stays in place, +N appears to right
      const formatBadge = (
        <span className="inline-flex items-center gap-1">
          <span
            className={`${colorStyle.bg} ${colorStyle.text} w-13 rounded-lg py-0.5 text-center text-[10px] font-semibold tracking-wide whitespace-nowrap sm:text-[11px]`}
          >
            {column.uppercase ? primaryFormat.toUpperCase() : primaryFormat}
          </span>
          {additionalFormats.length > 0 && (
            <span className="rounded-lg bg-gray-500/20 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-gray-700 sm:px-2 sm:text-[11px] dark:text-gray-300">
              +{additionalFormats.length}
            </span>
          )}
        </span>
      );

      return (
        <div className="flex items-center justify-start">
          {tooltipContent ? (
            <Tooltip content={tooltipContent} position="top">
              {formatBadge}
            </Tooltip>
          ) : (
            formatBadge
          )}
        </div>
      );
    }

    case 'number':
      if (compact) {
        return <span>{displayValue}</span>;
      }
      return (
        <div className={`flex items-center ${alignClass} text-xs text-gray-600 dark:text-gray-300`}>
          {displayValue}
        </div>
      );

    case 'text':
    default: {
      // Check if this is a server column with online status data
      const isServerColumn = column.key === 'extra.server' && onlineServers !== undefined;
      const isOnline = isServerColumn && onlineServers?.includes(cellValue);

      if (compact) {
        if (isServerColumn) {
          return (
            <span className="inline-flex items-center gap-1">
              <span
                className={`h-1.5 w-1.5 shrink-0 rounded-full ${isOnline ? 'bg-emerald-500' : 'bg-gray-400'}`}
                title={isOnline ? t('Online') : t('Offline')}
              />
              {displayValue}
            </span>
          );
        }
        return <span>{displayValue}</span>;
      }

      return (
        <div
          className={`flex items-center ${alignClass} truncate text-xs text-gray-600 dark:text-gray-300`}
        >
          {isServerColumn && (
            <span
              className={`mr-1.5 h-2 w-2 shrink-0 rounded-full ${isOnline ? 'bg-emerald-500' : 'bg-gray-400'}`}
              title={isOnline ? t('Online') : t('Offline')}
            />
          )}
          <span className="truncate" title={displayValue || undefined}>
            {displayValue}
          </span>
        </div>
      );
    }
  }
};

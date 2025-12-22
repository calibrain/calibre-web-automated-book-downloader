import { ColumnSchema, ColumnColorHint, Release } from '../types';
import { getFormatColor, getLanguageColor, getDownloadTypeColor, ColorStyle } from '../utils/colorMaps';

interface ReleaseCellProps {
  column: ColumnSchema;
  release: Release;
  compact?: boolean;  // When true, renders badges as plain text (for mobile info lines)
}

/**
 * Get a nested value from an object using dot-notation path.
 * e.g., getNestedValue(obj, "extra.language") returns obj.extra.language
 */
const getNestedValue = (obj: Record<string, unknown>, path: string): unknown => {
  return path.split('.').reduce((current, key) => {
    if (current && typeof current === 'object') {
      return (current as Record<string, unknown>)[key];
    }
    return undefined;
  }, obj as unknown);
};

const DEFAULT_COLOR_STYLE: ColorStyle = { bg: 'bg-gray-500/20', text: 'text-gray-700 dark:text-gray-300' };

/**
 * Get the color style for a value based on the color hint.
 */
const getColorStyle = (value: string, colorHint?: ColumnColorHint | null): ColorStyle => {
  if (!colorHint) return DEFAULT_COLOR_STYLE;

  if (colorHint.type === 'static') {
    // For static hints, assume it's a bg class and pair with default text
    return { bg: colorHint.value, text: 'text-gray-700 dark:text-gray-300' };
  }

  if (colorHint.type === 'map') {
    switch (colorHint.value) {
      case 'format':
        return getFormatColor(value);
      case 'language':
        return getLanguageColor(value);
      case 'download_type':
        return getDownloadTypeColor(value);
      default:
        return DEFAULT_COLOR_STYLE;
    }
  }

  return DEFAULT_COLOR_STYLE;
};

/**
 * Generic cell renderer for release list columns.
 * Renders different column types (text, badge, size, number, seeders) based on schema.
 * When compact=true, badges render as plain text for use in mobile info lines.
 */
export const ReleaseCell = ({ column, release, compact = false }: ReleaseCellProps) => {
  const rawValue = getNestedValue(release as unknown as Record<string, unknown>, column.key);
  const value = rawValue !== undefined && rawValue !== null
    ? String(rawValue)
    : column.fallback;

  const displayValue = column.uppercase ? value.toUpperCase() : value;

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
      const colorStyle = getColorStyle(value, column.color_hint);
      return (
        <div className={`flex items-center ${alignClass}`}>
          {value !== column.fallback ? (
            <span className={`${colorStyle.bg} ${colorStyle.text} text-[10px] sm:text-[11px] font-semibold px-1.5 sm:px-2 py-0.5 rounded-lg tracking-wide`}>
              {displayValue}
            </span>
          ) : (
            <span className="text-[10px] sm:text-xs text-gray-500 dark:text-gray-400">{column.fallback}</span>
          )}
        </div>
      );
    }

    case 'size':
      if (compact) {
        return <span>{displayValue}</span>;
      }
      return (
        <div className={`flex items-center ${alignClass} text-xs text-gray-600 dark:text-gray-300`}>
          {displayValue}
        </div>
      );

    case 'peers': {
      // Peers display: "S/L" string with badge colored by seeder count
      // Color logic: 0 = red, 1-10 = yellow, 10+ = blue
      const seeders = release.seeders;
      const peersValue = value || column.fallback;
      const isFallback = seeders == null || peersValue === column.fallback;

      // If no data, show plain text like badge type does
      if (isFallback) {
        if (compact) {
          return <span>{column.fallback}</span>;
        }
        return (
          <div className={`flex items-center ${alignClass}`}>
            <span className="text-[10px] sm:text-xs text-gray-500 dark:text-gray-400">{column.fallback}</span>
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
        return <span className={`font-medium ${badgeColors.split(' ').slice(1).join(' ')}`}>{peersValue}</span>;
      }
      return (
        <div className={`flex items-center ${alignClass}`}>
          <span className={`${badgeColors} text-[10px] sm:text-[11px] font-semibold px-1.5 sm:px-2 py-0.5 rounded-lg tracking-wide`}>
            {peersValue}
          </span>
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
    default:
      if (compact) {
        return <span>{displayValue}</span>;
      }
      return (
        <div className={`flex items-center ${alignClass} text-xs text-gray-600 dark:text-gray-300 truncate`}>
          {displayValue}
        </div>
      );
  }
};

export default ReleaseCell;

import { t } from '../../i18n';
import type { LibraryOwnership } from '../../types';

/** True when the library check reports this book held in any format. */
export function isInLibrary(library?: LibraryOwnership | null): boolean {
  if (!library) return false;
  return Object.values(library).some((holding) => holding === 'owned' || holding === 'collection');
}

/** True when every format that holds it holds it inside a larger volume. */
export function isCollectionOnly(library?: LibraryOwnership | null): boolean {
  if (!isInLibrary(library)) return false;
  return !Object.values(library ?? {}).includes('owned');
}

interface LibraryBadgeProps {
  library?: LibraryOwnership | null;
  /** Solid pill for use over cover art; otherwise a tinted inline pill. */
  overlay?: boolean;
  className?: string;
}

/** "Already in your library" badge. Renders nothing when the book is not held. */
export function LibraryBadge({ library, overlay = false, className = '' }: LibraryBadgeProps) {
  if (!isInLibrary(library)) return null;

  const collectionOnly = isCollectionOnly(library);
  const label = collectionOnly
    ? t('In your library, inside a collection')
    : t('Already in your library');
  const text = collectionOnly ? t('In a collection') : t('In library');

  const pill = overlay
    ? 'rounded-md border border-sky-700 bg-sky-600 px-1.5 py-0.5 text-[10px] font-bold text-white'
    : 'rounded-md bg-sky-600/15 px-1.5 py-0.5 text-[10px] font-semibold text-sky-700 dark:text-sky-300';
  const style = overlay
    ? { boxShadow: '0 2px 8px rgba(0, 0, 0, 0.4), 0 1px 3px rgba(0, 0, 0, 0.3)' }
    : undefined;

  return (
    <span
      className={`flex w-fit items-center gap-0.5 ${pill} ${className}`}
      style={style}
      title={label}
      aria-label={label}
    >
      <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
      </svg>
      {text}
    </span>
  );
}

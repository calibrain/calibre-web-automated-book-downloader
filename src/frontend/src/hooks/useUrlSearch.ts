import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { parseUrlSearchParams, ParsedUrlSearch } from '../utils/parseUrlSearchParams';

interface UseUrlSearchOptions {
  /** Only process URL params after auth check and config are loaded */
  enabled: boolean;
}

interface UseUrlSearchReturn {
  /** Parsed URL parameters, or null if none found */
  parsedParams: ParsedUrlSearch | null;
  /** Whether URL has been processed (regardless of whether params existed) */
  wasProcessed: boolean;
}

/**
 * Hook to parse URL search parameters on initial page load.
 *
 * This is a read-only operation - URL params are parsed once when enabled,
 * and the URL is not updated when users perform searches.
 *
 * @example
 * // In App.tsx:
 * const { parsedParams, wasProcessed } = useUrlSearch({
 *   enabled: isAuthenticated && config !== null,
 * });
 *
 * useEffect(() => {
 *   if (wasProcessed && parsedParams?.hasSearchParams) {
 *     // Trigger search with parsed params
 *   }
 * }, [wasProcessed, parsedParams]);
 */
export function useUrlSearch({ enabled }: UseUrlSearchOptions): UseUrlSearchReturn {
  const [searchParams] = useSearchParams();
  const processedRef = useRef(false);
  const parsedRef = useRef<ParsedUrlSearch | null>(null);

  useEffect(() => {
    if (enabled && !processedRef.current) {
      const parsed = parseUrlSearchParams(searchParams);
      if (parsed.hasSearchParams) {
        parsedRef.current = parsed;
      }
      processedRef.current = true;
    }
  }, [enabled, searchParams]);

  return {
    parsedParams: parsedRef.current,
    wasProcessed: processedRef.current,
  };
}

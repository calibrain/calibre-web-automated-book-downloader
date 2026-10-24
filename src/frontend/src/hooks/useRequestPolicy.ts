import { useCallback, useMemo, useState } from 'react';

import { fetchRequestPolicy } from '../services/api';
import type { RequestPolicyMode, RequestPolicyResponse } from '../types';
import { policyTrace } from '../utils/policyTrace';
import {
  DEFAULT_POLICY_TTL_MS,
  RequestPolicyCache,
  resolveDefaultModeFromPolicy,
  resolveSourceModeFromPolicy,
} from './requestPolicyCore';

interface UseRequestPolicyOptions {
  enabled: boolean;
  isAdmin: boolean;
  ttlMs?: number;
}

interface UseRequestPolicyReturn {
  policy: RequestPolicyResponse | null;
  isLoading: boolean;
  isAdmin: boolean;
  requestsEnabled: boolean;
  allowNotes: boolean;
  allowBookRequestWithDownload: boolean;
  getDefaultMode: (contentType: string) => RequestPolicyMode;
  getSourceMode: (source: string, contentType: string) => RequestPolicyMode;
  refresh: (options?: { force?: boolean }) => Promise<RequestPolicyResponse | null>;
}

export function useRequestPolicy({
  enabled,
  isAdmin,
  ttlMs = DEFAULT_POLICY_TTL_MS,
}: UseRequestPolicyOptions): UseRequestPolicyReturn {
  const [policy, setPolicy] = useState<RequestPolicyResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const cache = useMemo(() => new RequestPolicyCache(fetchRequestPolicy, ttlMs), [ttlMs]);

  const fetchPolicy = useCallback(
    async (force: boolean): Promise<RequestPolicyResponse | null> => {
      if (!enabled) {
        cache.reset();
        setPolicy(null);
        setIsLoading(false);
        return null;
      }

      setIsLoading(true);
      try {
        policyTrace('policy.refresh:start', { force, enabled, isAdmin });
        // Always fetch server policy while authenticated so backend auth state
        // remains authoritative even if local auth state is stale.
        const response = await cache.refresh({ enabled, isAdmin: false, force });
        policyTrace('policy.refresh:ok', {
          force,
          requestsEnabled: response?.requests_enabled ?? null,
          defaults: response?.defaults ?? null,
        });
        setPolicy(response);
        return response;
      } catch (error) {
        policyTrace('policy.refresh:error', {
          force,
          message: error instanceof Error ? error.message : String(error),
        });
        throw error;
      } finally {
        setIsLoading(false);
      }
    },
    [cache, enabled, isAdmin],
  );

  const getDefaultMode = useCallback(
    (contentType: string): RequestPolicyMode => {
      const effectivePolicy = enabled ? policy : null;
      const effectiveIsAdmin = effectivePolicy ? effectivePolicy.is_admin : isAdmin;
      return resolveDefaultModeFromPolicy(effectivePolicy, effectiveIsAdmin, contentType);
    },
    [enabled, policy, isAdmin],
  );

  const getSourceMode = useCallback(
    (source: string, contentType: string): RequestPolicyMode => {
      const effectivePolicy = enabled ? policy : null;
      const effectiveIsAdmin = effectivePolicy ? effectivePolicy.is_admin : isAdmin;
      return resolveSourceModeFromPolicy(effectivePolicy, effectiveIsAdmin, source, contentType);
    },
    [enabled, policy, isAdmin],
  );

  const refresh = useCallback(
    async (options: { force?: boolean } = {}) => {
      return fetchPolicy(options.force === true);
    },
    [fetchPolicy],
  );

  return {
    policy: enabled ? policy : null,
    isLoading: enabled ? isLoading : false,
    isAdmin: enabled && policy ? policy.is_admin : isAdmin,
    requestsEnabled: enabled ? (policy?.requests_enabled ?? false) : false,
    allowNotes: enabled ? (policy?.allow_notes ?? true) : true,
    allowBookRequestWithDownload: enabled
      ? (policy?.allow_book_request_with_download ?? false)
      : false,
    getDefaultMode,
    getSourceMode,
    refresh,
  };
}

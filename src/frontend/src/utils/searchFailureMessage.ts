import { t } from '../i18n';
import { isApiResponseError, isTimeoutError } from '../services/api';

// Shown when we genuinely have nothing better: the request failed without the server
// saying why, which really can mean blocked mirrors or a restricted network.
export const UNREACHABLE_SOURCE_MESSAGE =
  'Unable to reach download source. Network may be restricted or mirrors blocked.';

// Shown when the client's own abort fired. It is the backstop for a server that never
// answered at all, and it says nothing about mirrors - the budget the client waited out
// is the server's own, which the user can raise. See issue #1285.
export const CLIENT_TIMEOUT_MESSAGE =
  'The search took longer than the server said it would. Raise the release search ' +
  'timeout if your setup is simply slow.';

// Shown when a gateway in front of Shelfmark gave up waiting on the search. Shelfmark
// never sends these statuses itself - a search it cannot finish ends in a 503 that says
// why - so the proxy's read timeout is shorter than the search budget. See discussion
// #1520, where nginx's 60s default cut off every search that needed a protection solve.
export const PROXY_TIMEOUT_MESSAGE =
  'A reverse proxy in front of Shelfmark stopped waiting before the search finished. ' +
  "Raise the proxy's read timeout to at least the release search timeout.";

// 504 Gateway Timeout, and Cloudflare's 524 for an origin that answered too slowly.
const PROXY_TIMEOUT_STATUSES = new Set([504, 524]);

// Statuses fetchJSON already words as the server or its proxy being unavailable.
const GATEWAY_UNAVAILABLE_STATUSES = new Set([502, 503]);

/**
 * The sentence to show for a failed direct-mode search.
 *
 * The server knows why a search failed - a spent search budget, an unsolved protection
 * challenge - and blanket-replacing that with the mirrors line told users their network
 * was broken when it was not. So: the server's own words when it explained itself, the
 * timeout line when we gave up before it answered, the proxy's part when a gateway
 * answered instead of Shelfmark, and the mirrors line only when none of those applies.
 * See issue #1285 and discussion #1520.
 */
export const describeSearchFailure = (error: unknown): string => {
  if (isTimeoutError(error)) {
    return t(CLIENT_TIMEOUT_MESSAGE);
  }

  if (isApiResponseError(error) && error.serverMessage) {
    return error.serverMessage;
  }

  if (isApiResponseError(error)) {
    if (PROXY_TIMEOUT_STATUSES.has(error.status)) {
      return PROXY_TIMEOUT_MESSAGE;
    }
    // A 502/503 without a JSON body is a proxy's error page, not Shelfmark's API: the
    // upstream is down or restarting, which says nothing about mirrors either.
    if (GATEWAY_UNAVAILABLE_STATUSES.has(error.status) && !error.payload) {
      return error.message;
    }
  }

  const message = error instanceof Error ? error.message : '';
  if (message.includes('Network restricted') || message.includes('Unable to reach')) {
    return message;
  }

  return t(UNREACHABLE_SOURCE_MESSAGE);
};

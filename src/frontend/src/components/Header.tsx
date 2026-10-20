import { useState, useRef, useMemo, forwardRef, useImperativeHandle } from 'react';

import { useDismiss } from '../hooks/useDismiss';
import { useMountEffect } from '../hooks/useMountEffect';
import type {
  ContentType,
  ActingAsUserSelection,
  MetadataSearchField,
  QueryTargetOption,
} from '../types';
import { formatActingAsUserName } from '../utils/actingAsUser';
import type { ActivityStatusCounts } from '../utils/activityBadge';
import { getActivityBadgeState } from '../utils/activityBadge';
import { withBasePath } from '../utils/basePath';
import { isRecord } from '../utils/objectHelpers';
import { DropdownList } from './DropdownList';
import type { SearchBarHandle } from './SearchBar';
import { SearchBar } from './SearchBar';

interface HeaderHandle {
  submitSearch: () => void;
}

interface HeaderProps {
  calibreWebUrl?: string;
  audiobookLibraryUrl?: string;
  debug?: boolean;
  logoUrl?: string;
  showSearch?: boolean;
  searchInput?: string | number | boolean;
  searchInputLabel?: string;
  onSearchChange?: (value: string | number | boolean, label?: string) => void;
  onSearch?: () => void;
  onAdvancedToggle?: () => void;
  isAdvancedActive?: boolean;
  isLoading?: boolean;
  onDownloadsClick?: () => void;
  onSettingsClick?: () => void;
  isAdmin?: boolean;
  canAccessSettings?: boolean;
  statusCounts?: ActivityStatusCounts;
  onLogoClick?: () => void;
  authRequired?: boolean;
  isAuthenticated?: boolean;
  username?: string | null;
  displayName?: string | null;
  actingAsUser?: ActingAsUserSelection | null;
  onActingAsUserChange?: (user: ActingAsUserSelection | null) => void;
  adminUsers?: ActingAsUserSelection[];
  isAdminUsersLoading?: boolean;
  adminUsersError?: string | null;
  hasLoadedAdminUsers?: boolean;
  onLoadAdminUsers?: () => Promise<void> | void;
  onLogout?: () => void;
  onShowToast?: (
    message: string,
    type: 'success' | 'error' | 'info',
    persistent?: boolean,
  ) => string;
  onRemoveToast?: (id: string) => void;
  contentType?: ContentType;
  onContentTypeChange?: (type: ContentType) => void;
  allowedContentTypes?: ContentType[];
  combinedMode?: boolean;
  combinedModeLocked?: boolean;
  onCombinedModeChange?: (enabled: boolean) => void;
  queryTargets?: QueryTargetOption[];
  activeQueryTarget?: string;
  onQueryTargetChange?: (target: string) => void;
  activeQueryField?: MetadataSearchField | null;
}

const applyTheme = (preference: string): void => {
  let effective = preference;
  if (preference === 'auto') {
    effective = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  document.documentElement.setAttribute('data-theme', effective);
  document.documentElement.style.colorScheme = effective;
};

const DEFAULT_STATUS_COUNTS: ActivityStatusCounts = {
  ongoing: 0,
  completed: 0,
  errored: 0,
  pendingRequests: 0,
};
const EMPTY_ADMIN_USERS: ActingAsUserSelection[] = [];
const EMPTY_QUERY_TARGETS: QueryTargetOption[] = [];

export const Header = forwardRef<HeaderHandle, HeaderProps>(
  (
    {
      calibreWebUrl,
      audiobookLibraryUrl,
      debug,
      logoUrl,
      showSearch = false,
      searchInput = '',
      searchInputLabel,
      onSearchChange,
      onSearch,
      onAdvancedToggle,
      isAdvancedActive = false,
      isLoading = false,
      onDownloadsClick,
      onSettingsClick,
      isAdmin = false,
      canAccessSettings,
      statusCounts = DEFAULT_STATUS_COUNTS,
      onLogoClick,
      authRequired = false,
      isAuthenticated = false,
      username,
      displayName,
      actingAsUser = null,
      onActingAsUserChange,
      adminUsers = EMPTY_ADMIN_USERS,
      isAdminUsersLoading = false,
      adminUsersError = null,
      hasLoadedAdminUsers = false,
      onLoadAdminUsers,
      onLogout,
      onShowToast,
      onRemoveToast,
      contentType = 'ebook',
      onContentTypeChange,
      allowedContentTypes,
      combinedMode,
      combinedModeLocked,
      onCombinedModeChange,
      queryTargets = EMPTY_QUERY_TARGETS,
      activeQueryTarget = 'general',
      onQueryTargetChange,
      activeQueryField = null,
    },
    ref,
  ) => {
    const activityBadge = getActivityBadgeState(statusCounts, isAdmin);
    const settingsEnabled = canAccessSettings ?? isAdmin;
    const searchBarRef = useRef<SearchBarHandle>(null);

    useImperativeHandle(ref, () => ({
      submitSearch: () => {
        searchBarRef.current?.submit();
      },
    }));
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const [isClosing, setIsClosing] = useState(false);
    const [shouldAnimateIn, setShouldAnimateIn] = useState(false);
    let dropdownAnimationClass = '';
    if (isClosing) {
      dropdownAnimationClass = 'animate-fade-out-up';
    } else if (shouldAnimateIn) {
      dropdownAnimationClass = 'animate-fade-in-down';
    }
    const dropdownRef = useRef<HTMLDivElement>(null);

    const actingAsOptions = useMemo(
      () => [
        { value: '', label: 'Myself' },
        ...adminUsers.map((user) => {
          const displayLabel = formatActingAsUserName(user);
          return {
            value: String(user.id),
            label: displayLabel,
            description: displayLabel !== user.username ? `@${user.username}` : undefined,
          };
        }),
      ],
      [adminUsers],
    );

    const selectedActingAsValue = actingAsUser ? String(actingAsUser.id) : '';
    const dropdownPanelWidthClass = 'w-48';

    useMountEffect(() => {
      const saved = localStorage.getItem('preferred-theme') || 'auto';
      applyTheme(saved);

      // Remove preload class and inline theme-init styles now that the
      // external CSS is loaded and React has mounted.
      requestAnimationFrame(() => {
        document.documentElement.classList.remove('preload');
        document.getElementById('theme-init')?.remove();
      });
    });

    useMountEffect(() => {
      const mq = window.matchMedia('(prefers-color-scheme: dark)');
      const handler = (e: MediaQueryListEvent) => {
        if (localStorage.getItem('preferred-theme') === 'auto') {
          const effective = e.matches ? 'dark' : 'light';
          document.documentElement.setAttribute('data-theme', effective);
          document.documentElement.style.colorScheme = effective;
        }
      };
      mq.addEventListener('change', handler);
      return () => mq.removeEventListener('change', handler);
    });

    // Helper function to close dropdown with animation
    const closeDropdown = () => {
      setIsClosing(true);
      setTimeout(() => {
        setIsDropdownOpen(false);
        setIsClosing(false);
      }, 150); // Match the animation duration
    };

    useDismiss(isDropdownOpen && !isClosing, [dropdownRef], closeDropdown);

    const handleLogout = () => {
      closeDropdown();
      onLogout?.();
    };

    const toggleDropdown = () => {
      if (isDropdownOpen) {
        closeDropdown();
      } else {
        if (isAdmin && !hasLoadedAdminUsers && !isAdminUsersLoading) {
          void onLoadAdminUsers?.();
        }
        setShouldAnimateIn(true);
        setIsDropdownOpen(true);
        // Reset animation flag after animation completes
        setTimeout(() => setShouldAnimateIn(false), 200);
      }
    };

    const handleHeaderSearch = () => {
      onSearch?.();
    };

    const handleSearchChange = (value: string | number | boolean, label?: string) => {
      onSearchChange?.(value, label);
    };

    const handleActingAsChange = (nextValue: string[] | string) => {
      if (Array.isArray(nextValue)) {
        return;
      }

      if (nextValue === '') {
        onActingAsUserChange?.(null);
        return;
      }

      const selectedUser = adminUsers.find((user) => String(user.id) === nextValue);
      if (!selectedUser) {
        return;
      }

      onActingAsUserChange?.(selectedUser);
    };

    const handleDebugDownload = async () => {
      closeDropdown();
      // Show persistent toast while gathering logs
      const loadingToastId = onShowToast?.(
        'Gathering debug logs... This may take a minute.',
        'info',
        true,
      );
      try {
        const response = await fetch(withBasePath('/api/debug'), {
          method: 'GET',
          credentials: 'include',
        });

        // Remove the loading toast
        if (loadingToastId) onRemoveToast?.(loadingToastId);

        if (!response.ok) {
          const errorData: unknown = await response.json().catch(() => null);
          const errorMessage =
            isRecord(errorData) && typeof errorData.error === 'string'
              ? errorData.error
              : response.statusText;
          onShowToast?.(`Debug download failed: ${errorMessage}`, 'error');
          return;
        }

        // Get the filename from Content-Disposition header or use default
        const contentDisposition = response.headers.get('Content-Disposition');
        let filename = 'debug.zip';
        if (contentDisposition) {
          const filenameMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
          if (filenameMatch && filenameMatch[1]) {
            filename = filenameMatch[1].replace(/['"]/g, '');
          }
        }

        // Create blob and trigger download
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        a.remove();

        onShowToast?.('Debug logs downloaded successfully', 'success');
      } catch (error) {
        // Remove the loading toast on error too
        if (loadingToastId) onRemoveToast?.(loadingToastId);
        console.error('Debug download error:', error);
        onShowToast?.('Debug download failed. Check console for details.', 'error');
      }
    };

    // Determine if we should show icons only (both URLs configured)
    const showIconsOnly = Boolean(calibreWebUrl && audiobookLibraryUrl);

    const renderSettingsButton = (variant: 'nav' | 'menu') => {
      if (!onSettingsClick) return null;
      const isNav = variant === 'nav';
      return (
        <button
          type="button"
          onClick={
            settingsEnabled
              ? () => {
                  if (!isNav) closeDropdown();
                  onSettingsClick();
                }
              : undefined
          }
          disabled={!settingsEnabled}
          className={
            isNav
              ? `hover-action relative rounded-full p-2 text-gray-900 transition-colors dark:text-gray-100 ${
                  settingsEnabled ? '' : 'cursor-not-allowed opacity-40'
                }`
              : `flex w-full items-center gap-3 px-4 py-2 text-left transition-colors ${
                  settingsEnabled ? 'hover-surface' : 'cursor-not-allowed opacity-40'
                }`
          }
          aria-label="Settings"
          title={isNav ? 'Settings' : undefined}
        >
          <svg
            className="h-5 w-5"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth="1.5"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281z"
            />
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
            />
          </svg>
          {!isNav && <span>Settings</span>}
        </button>
      );
    };

    // Icon buttons - reused for both states
    const iconButtonsNode = (
      <div className="flex items-center gap-2">
        {/* Book Library Button */}
        {calibreWebUrl && (
          <a
            href={calibreWebUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="hover-action flex items-center gap-2 rounded-full px-3 py-2 text-gray-900 transition-all duration-200 dark:text-gray-100"
            aria-label="Open book library"
            title={showIconsOnly ? 'Book Library' : 'Go To Library'}
          >
            <svg
              className="h-5 w-5"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25"
              />
            </svg>
            {!showIconsOnly && <span className="text-sm font-medium">Go To Library</span>}
          </a>
        )}

        {/* Audiobook Library Button */}
        {audiobookLibraryUrl && (
          <a
            href={audiobookLibraryUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="hover-action flex items-center gap-2 rounded-full px-3 py-2 text-gray-900 transition-all duration-200 dark:text-gray-100"
            aria-label="Open audiobook library"
            title={showIconsOnly ? 'Audiobook Library' : 'Go To Library'}
          >
            <svg
              className="h-5 w-5"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z"
              />
            </svg>
            {!showIconsOnly && <span className="text-sm font-medium">Go To Library</span>}
          </a>
        )}

        {/* Activity Button */}
        {onDownloadsClick && (
          <button
            type="button"
            onClick={onDownloadsClick}
            className="hover-action relative flex items-center gap-2 rounded-full px-3 py-2 text-gray-900 transition-all duration-200 dark:text-gray-100"
            aria-label="View activity"
            title="Activity"
          >
            <div className="relative">
              <svg
                className="h-5 w-5"
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth="1.5"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3"
                />
              </svg>
              {activityBadge && (
                <span
                  className={`absolute -top-1 -right-1 flex h-3.5 w-3.5 items-center justify-center rounded-full text-[0.55rem] font-bold text-white ${activityBadge.colorClass}`}
                  title={activityBadge.title}
                >
                  {activityBadge.total}
                </span>
              )}
            </div>
            <span className="hidden text-sm font-medium sm:inline">Activity</span>
          </button>
        )}

        {renderSettingsButton('nav')}

        {/* User Menu Dropdown */}
        <div className="relative" ref={dropdownRef}>
          <button
            type="button"
            onClick={toggleDropdown}
            className={`hover-action relative rounded-full p-2 transition-colors ${
              isDropdownOpen ? 'bg-(--hover-action)' : ''
            }`}
            aria-label="User menu"
            aria-expanded={isDropdownOpen}
            aria-haspopup="true"
          >
            <svg
              className="h-5 w-5"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5"
              />
            </svg>
            {actingAsUser && (
              <span
                className="absolute top-1 right-1 h-2 w-2 rounded-full border border-(--bg) bg-sky-500"
                title={`Downloading as ${formatActingAsUserName(actingAsUser)}`}
              />
            )}
          </button>

          {/* Dropdown Menu */}
          {(isDropdownOpen || isClosing) && (
            <div
              className={`absolute right-0 mt-2 ${dropdownPanelWidthClass} z-50 rounded-lg border shadow-lg ${
                dropdownAnimationClass
              }`}
              style={{
                background: 'var(--bg)',
                borderColor: 'var(--border-muted)',
              }}
            >
              <div className="py-1">
                <a
                  href="https://github.com/calibrain/shelfmark/issues"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover-surface flex w-full items-center gap-3 px-4 py-2 text-left text-slate-700 transition-colors dark:text-slate-200"
                  title="Submit a bug report"
                >
                  <svg
                    className="h-5 w-5"
                    xmlns="http://www.w3.org/2000/svg"
                    fill="none"
                    viewBox="0 0 24 24"
                    strokeWidth="1.5"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M3 3v1.5M3 21v-6m0 0 2.77-.693a9 9 0 0 1 6.208.682l.108.054a9 9 0 0 0 6.086.71l3.114-.732a48.524 48.524 0 0 1-.005-10.499l-3.11.732a9 9 0 0 1-6.085-.711l-.108-.054a9 9 0 0 0-6.208-.682L3 4.5M3 15V4.5"
                    />
                  </svg>
                  <span>Report a Bug</span>
                </a>

                {/* Settings Button */}
                {renderSettingsButton('menu')}

                {/* Debug Buttons */}
                {debug && (
                  <>
                    <button
                      type="button"
                      className="hover-surface flex w-full items-center gap-3 px-4 py-2 text-left text-orange-600 transition-colors dark:text-orange-400"
                      onClick={() => void handleDebugDownload()}
                    >
                      <svg
                        className="h-5 w-5"
                        xmlns="http://www.w3.org/2000/svg"
                        fill="none"
                        viewBox="0 0 24 24"
                        strokeWidth="1.5"
                        stroke="currentColor"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M12 12.75c1.148 0 2.278.08 3.383.237 1.037.146 1.866.966 1.866 2.013 0 3.728-2.35 6.75-5.25 6.75S6.75 18.728 6.75 15c0-1.046.83-1.867 1.866-2.013A24.204 24.204 0 0112 12.75zm0 0c2.883 0 5.647.508 8.207 1.44a23.91 23.91 0 01-1.152 6.06M12 12.75c-2.883 0-5.647.508-8.208 1.44.125 2.104.52 4.136 1.153 6.06M12 12.75a2.25 2.25 0 002.248-2.354M12 12.75a2.25 2.25 0 01-2.248-2.354M12 8.25c.995 0 1.971-.08 2.922-.236.403-.066.74-.358.795-.762a3.778 3.778 0 00-.399-2.25M12 8.25c-.995 0-1.97-.08-2.922-.236-.402-.066-.74-.358-.795-.762a3.734 3.734 0 01.4-2.253M12 8.25a2.25 2.25 0 00-2.248 2.146M12 8.25a2.25 2.25 0 012.248 2.146M8.683 5a6.032 6.032 0 01-1.155-1.002c.07-.63.27-1.222.574-1.747m.581 2.749A3.75 3.75 0 0115.318 5m0 0c.427-.283.815-.62 1.155-.999a4.471 4.471 0 00-.575-1.752M4.921 6a24.048 24.048 0 00-.392 3.314c1.668.546 3.416.914 5.223 1.082M19.08 6c.205 1.08.337 2.187.392 3.314a23.882 23.882 0 01-5.223 1.082"
                        />
                      </svg>
                      <span>Debug</span>
                    </button>
                    <form action={withBasePath('/api/restart')} method="get" className="w-full">
                      <button
                        className="hover-surface flex w-full items-center gap-3 px-4 py-2 text-left text-orange-600 transition-colors dark:text-orange-400"
                        type="submit"
                      >
                        <svg
                          className="h-5 w-5"
                          xmlns="http://www.w3.org/2000/svg"
                          fill="none"
                          viewBox="0 0 24 24"
                          strokeWidth="1.5"
                          stroke="currentColor"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99"
                          />
                        </svg>
                        <span>Restart</span>
                      </button>
                    </form>
                  </>
                )}

                {/* User Footer */}
                {authRequired && isAuthenticated && username && (
                  <div className="border-t" style={{ borderColor: 'var(--border-muted)' }}>
                    <div className="flex items-center gap-2.5 px-4 py-3">
                      <span
                        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold uppercase"
                        style={{ backgroundColor: 'var(--hover-surface)', color: 'var(--text)' }}
                      >
                        {(displayName || username).slice(0, 2)}
                      </span>
                      <div className="min-w-0 flex-1 truncate text-sm font-medium">
                        {displayName || username}
                      </div>
                      {onLogout && (
                        <button
                          type="button"
                          onClick={handleLogout}
                          className="hover-action shrink-0 rounded-full p-2 text-red-600 transition-colors dark:text-red-400"
                          title="Sign Out"
                          aria-label="Sign Out"
                        >
                          <svg
                            className="h-5 w-5"
                            xmlns="http://www.w3.org/2000/svg"
                            fill="none"
                            viewBox="0 0 24 24"
                            strokeWidth="1.5"
                            stroke="currentColor"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9"
                            />
                          </svg>
                        </button>
                      )}
                    </div>
                  </div>
                )}

                {isAdmin && onActingAsUserChange && (
                  <div
                    className="space-y-2 border-t px-4 py-3"
                    style={{ borderColor: 'var(--border-muted)' }}
                  >
                    <div className="text-xs font-medium tracking-wide uppercase opacity-70">
                      Download as
                    </div>
                    <div className={isAdminUsersLoading ? 'pointer-events-none opacity-60' : ''}>
                      <DropdownList
                        options={actingAsOptions}
                        value={selectedActingAsValue}
                        onChange={handleActingAsChange}
                        placeholder="Myself"
                        widthClassName="w-full"
                        buttonClassName="rounded-lg text-sm"
                      />
                    </div>
                    {isAdminUsersLoading && (
                      <div className="text-xs opacity-70">Loading users...</div>
                    )}
                    {adminUsersError && (
                      <div className="flex items-center justify-between gap-3">
                        <div className="text-xs text-red-600 dark:text-red-400">
                          {adminUsersError}
                        </div>
                        <button
                          type="button"
                          onClick={() => void onLoadAdminUsers?.()}
                          className="text-xs font-medium text-sky-600 hover:text-sky-700 dark:text-sky-400 dark:hover:text-sky-300"
                        >
                          Retry
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    );

    return (
      <header
        className="sticky top-0 z-40 w-full"
        style={{ background: 'var(--bg)', paddingTop: 'env(safe-area-inset-top)' }}
      >
        <div className="mx-auto max-w-full px-4 py-4 sm:px-6 lg:px-8">
          {/* When search is active: stack on mobile, side-by-side on desktop */}
          {showSearch && (
            <div className="animate-pop-up flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              {/* Logo + Icon buttons - appear first on mobile (above search), last on desktop (right side) */}
              <div className="flex w-full items-center justify-between lg:order-2 lg:w-auto lg:justify-end">
                {/* Logo - visible on mobile only, aligned left */}
                {logoUrl &&
                  (onLogoClick ? (
                    <button
                      type="button"
                      onClick={onLogoClick}
                      className="shrink-0 cursor-pointer border-0 bg-transparent p-0 lg:hidden"
                      aria-label="Reset search"
                    >
                      <img src={logoUrl} alt="" className="h-10 w-10" />
                    </button>
                  ) : (
                    <img
                      src={logoUrl}
                      alt="Shelfmark logo"
                      className="h-10 w-10 shrink-0 lg:hidden"
                    />
                  ))}

                {iconButtonsNode}
              </div>

              {/* Search bar - appear second on mobile (below logo+icons), first on desktop (left side) */}
              <div className="flex flex-1 items-center gap-4 lg:order-1">
                {/* Logo - visible on desktop only, aligned with search */}
                {logoUrl &&
                  (onLogoClick ? (
                    <button
                      type="button"
                      onClick={onLogoClick}
                      className="hidden shrink-0 cursor-pointer border-0 bg-transparent p-0 lg:block"
                      aria-label="Reset search"
                    >
                      <img src={logoUrl} alt="" className="h-12 w-12" />
                    </button>
                  ) : (
                    <img
                      src={logoUrl}
                      alt="Shelfmark logo"
                      className="hidden h-12 w-12 shrink-0 lg:block"
                    />
                  ))}
                <SearchBar
                  ref={searchBarRef}
                  className="flex-1 lg:w-[calc(50vw+5rem)] lg:flex-none"
                  value={searchInput}
                  valueLabel={searchInputLabel}
                  onChange={handleSearchChange}
                  onSubmit={handleHeaderSearch}
                  onAdvancedToggle={onAdvancedToggle}
                  isAdvancedActive={isAdvancedActive}
                  isLoading={isLoading}
                  contentType={contentType}
                  onContentTypeChange={onContentTypeChange}
                  allowedContentTypes={allowedContentTypes}
                  combinedMode={combinedMode}
                  combinedModeLocked={combinedModeLocked}
                  onCombinedModeChange={onCombinedModeChange}
                  queryTargets={queryTargets}
                  activeQueryTarget={activeQueryTarget}
                  onQueryTargetChange={onQueryTargetChange}
                  activeQueryField={activeQueryField}
                />
              </div>
            </div>
          )}

          {/* When search is NOT active: show icon buttons only on the right */}
          {!showSearch && (
            <div className="flex min-h-[48px] items-center justify-end">{iconButtonsNode}</div>
          )}
        </div>
      </header>
    );
  },
);

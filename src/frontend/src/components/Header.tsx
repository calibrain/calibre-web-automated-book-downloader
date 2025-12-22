import { useState, useEffect, useRef, forwardRef, useImperativeHandle } from 'react';
import { SearchBar, SearchBarHandle } from './SearchBar';

export interface HeaderHandle {
  submitSearch: () => void;
}

interface StatusCounts {
  ongoing: number;
  completed: number;
  errored: number;
}

interface HeaderProps {
  calibreWebUrl?: string;
  debug?: boolean;
  logoUrl?: string;
  showSearch?: boolean;
  searchInput?: string;
  onSearchChange?: (value: string) => void;
  onSearch?: () => void;
  onAdvancedToggle?: () => void;
  isLoading?: boolean;
  onDownloadsClick?: () => void;
  onSettingsClick?: () => void;
  statusCounts?: StatusCounts;
  onLogoClick?: () => void;
  authRequired?: boolean;
  isAuthenticated?: boolean;
  onLogout?: () => void;
  onShowToast?: (message: string, type: 'success' | 'error' | 'info', persistent?: boolean) => string;
  onRemoveToast?: (id: string) => void;
}

export const Header = forwardRef<HeaderHandle, HeaderProps>(({
  calibreWebUrl,
  debug,
  logoUrl,
  showSearch = false,
  searchInput = '',
  onSearchChange,
  onSearch,
  onAdvancedToggle,
  isLoading = false,
  onDownloadsClick,
  onSettingsClick,
  statusCounts = { ongoing: 0, completed: 0, errored: 0 },
  onLogoClick,
  authRequired = false,
  isAuthenticated = false,
  onLogout,
  onShowToast,
  onRemoveToast,
}, ref) => {
  const searchBarRef = useRef<SearchBarHandle>(null);

  useImperativeHandle(ref, () => ({
    submitSearch: () => {
      searchBarRef.current?.submit();
    },
  }));
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  const [shouldAnimateIn, setShouldAnimateIn] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const saved = localStorage.getItem('preferred-theme') || 'auto';
    applyTheme(saved);

    // Remove preload class after initial theme is applied to enable transitions
    requestAnimationFrame(() => {
      document.documentElement.classList.remove('preload');
    });
  }, []);

  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = (e: MediaQueryListEvent) => {
      if (localStorage.getItem('preferred-theme') === 'auto') {
        document.documentElement.setAttribute('data-theme', e.matches ? 'dark' : 'light');
      }
    };
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);

  // Helper function to close dropdown with animation
  const closeDropdown = () => {
    setIsClosing(true);
    setTimeout(() => {
      setIsDropdownOpen(false);
      setIsClosing(false);
    }, 150); // Match the animation duration
  };

  // Close dropdown when clicking outside or pressing ESC
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        closeDropdown();
      }
    };

    const handleEscapeKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeDropdown();
      }
    };

    if (isDropdownOpen && !isClosing) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleEscapeKey);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscapeKey);
    };
  }, [isDropdownOpen, isClosing]);

  const applyTheme = (pref: string) => {
    if (pref === 'auto') {
      const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
    } else {
      document.documentElement.setAttribute('data-theme', pref);
    }
  };

  const handleLogout = () => {
    closeDropdown();
    onLogout?.();
  };

  const toggleDropdown = () => {
    if (isDropdownOpen) {
      closeDropdown();
    } else {
      setShouldAnimateIn(true);
      setIsDropdownOpen(true);
      // Reset animation flag after animation completes
      setTimeout(() => setShouldAnimateIn(false), 200);
    }
  };

  const handleHeaderSearch = () => {
    onSearch?.();
  };

  const handleSearchChange = (value: string) => {
    onSearchChange?.(value);
  };

  // Icon buttons component - reused for both states
  const IconButtons = () => (
    <div className="flex items-center gap-2">
      {/* Calibre-Web Button */}
      {calibreWebUrl && (
        <a
          href={calibreWebUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 px-3 py-2 rounded-full hover-action transition-all duration-200 text-gray-900 dark:text-gray-100"
          aria-label="Open Calibre-Web"
          title="Go To Library"
        >
          <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
          </svg>
          <span className="text-sm font-medium">Go To Library</span>
        </a>
      )}

      {/* Downloads Button */}
      {onDownloadsClick && (
        <button
          onClick={onDownloadsClick}
          className="relative flex items-center gap-2 px-3 py-2 rounded-full hover-action transition-all duration-200 text-gray-900 dark:text-gray-100"
          aria-label="View downloads"
          title="Downloads"
        >
          <div className="relative">
            <svg
              className="w-5 h-5"
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
            {/* Show badge with appropriate color based on status */}
            {(statusCounts.ongoing > 0 || statusCounts.completed > 0 || statusCounts.errored > 0) && (
            <span
              className={`absolute -top-1 -right-1 text-white text-[0.55rem] font-bold rounded-full w-3.5 h-3.5 flex items-center justify-center ${
                  statusCounts.errored > 0
                    ? 'bg-red-500'
                    : statusCounts.ongoing > 0
                    ? 'bg-blue-500'
                    : 'bg-green-500'
                }`}
                title={`${statusCounts.ongoing} ongoing, ${statusCounts.completed} completed, ${statusCounts.errored} failed`}
              >
                {statusCounts.ongoing + statusCounts.completed + statusCounts.errored}
              </span>
            )}
          </div>
          <span className="hidden sm:inline text-sm font-medium">Downloads</span>
        </button>
      )}

      {/* User Menu Dropdown */}
      <div className="relative" ref={dropdownRef}>
        <button
          onClick={toggleDropdown}
          className={`relative p-2 rounded-full hover-action transition-colors ${
            isDropdownOpen ? 'bg-gray-100 dark:bg-gray-700' : ''
          }`}
          aria-label="User menu"
          aria-expanded={isDropdownOpen}
          aria-haspopup="true"
        >
          <svg
            className="w-5 h-5"
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
        </button>

        {/* Dropdown Menu */}
        {(isDropdownOpen || isClosing) && (
          <div
            className={`absolute right-0 mt-2 w-48 rounded-lg shadow-lg border z-50 ${
              isClosing ? 'animate-fade-out-up' : shouldAnimateIn ? 'animate-fade-in-down' : ''
            }`}
            style={{
              background: 'var(--bg)',
              borderColor: 'var(--border-muted)',
            }}
          >
            <div className="py-1">
              <a
                href="https://github.com/calibrain/calibre-web-automated-book-downloader/issues"
                target="_blank"
                rel="noopener noreferrer"
                className="w-full text-left px-4 py-2 hover-surface transition-colors flex items-center gap-3 text-slate-700 dark:text-slate-200"
                title="Submit a bug report"
              >
                <svg
                  className="w-5 h-5"
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
              {onSettingsClick && (
                <button
                  type="button"
                  onClick={() => {
                    closeDropdown();
                    onSettingsClick();
                  }}
                  className="w-full text-left px-4 py-2 hover-surface transition-colors flex items-center gap-3"
                >
                  <svg
                    className="w-5 h-5"
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
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                  </svg>
                  <span>Settings</span>
                </button>
              )}

              {/* Debug Buttons */}
              {debug && (
                <>
                  <button
                    className="w-full text-left px-4 py-2 hover-surface transition-colors flex items-center gap-3 text-orange-600 dark:text-orange-400"
                    onClick={async () => {
                      closeDropdown();
                      // Show persistent toast while gathering logs
                      const loadingToastId = onShowToast?.('Gathering debug logs... This may take a minute.', 'info', true);
                      try {
                        const response = await fetch('/api/debug', {
                          method: 'GET',
                          credentials: 'include',
                        });

                        // Remove the loading toast
                        if (loadingToastId) onRemoveToast?.(loadingToastId);

                        if (!response.ok) {
                          const errorData = await response.json().catch(() => ({}));
                          onShowToast?.(`Debug download failed: ${errorData.error || response.statusText}`, 'error');
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
                    }}
                  >
                    <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M12 12.75c1.148 0 2.278.08 3.383.237 1.037.146 1.866.966 1.866 2.013 0 3.728-2.35 6.75-5.25 6.75S6.75 18.728 6.75 15c0-1.046.83-1.867 1.866-2.013A24.204 24.204 0 0112 12.75zm0 0c2.883 0 5.647.508 8.207 1.44a23.91 23.91 0 01-1.152 6.06M12 12.75c-2.883 0-5.647.508-8.208 1.44.125 2.104.52 4.136 1.153 6.06M12 12.75a2.25 2.25 0 002.248-2.354M12 12.75a2.25 2.25 0 01-2.248-2.354M12 8.25c.995 0 1.971-.08 2.922-.236.403-.066.74-.358.795-.762a3.778 3.778 0 00-.399-2.25M12 8.25c-.995 0-1.97-.08-2.922-.236-.402-.066-.74-.358-.795-.762a3.734 3.734 0 01.4-2.253M12 8.25a2.25 2.25 0 00-2.248 2.146M12 8.25a2.25 2.25 0 012.248 2.146M8.683 5a6.032 6.032 0 01-1.155-1.002c.07-.63.27-1.222.574-1.747m.581 2.749A3.75 3.75 0 0115.318 5m0 0c.427-.283.815-.62 1.155-.999a4.471 4.471 0 00-.575-1.752M4.921 6a24.048 24.048 0 00-.392 3.314c1.668.546 3.416.914 5.223 1.082M19.08 6c.205 1.08.337 2.187.392 3.314a23.882 23.882 0 01-5.223 1.082" />
                    </svg>
                    <span>Debug</span>
                  </button>
                  <form action="/api/restart" method="get" className="w-full">
                    <button
                      className="w-full text-left px-4 py-2 hover-surface transition-colors flex items-center gap-3 text-orange-600 dark:text-orange-400"
                      type="submit"
                    >
                      <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
                      </svg>
                      <span>Restart</span>
                    </button>
                  </form>
                </>
              )}

              {/* Logout Button */}
              {authRequired && isAuthenticated && onLogout && (
                <button
                  type="button"
                  onClick={handleLogout}
                  className="w-full text-left px-4 py-2 hover-surface transition-colors flex items-center gap-3 text-red-600 dark:text-red-400"
                >
                  <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15M12 9l-3 3m0 0l3 3m-3-3h12.75" />
                  </svg>
                  <span>Sign Out</span>
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );

  return (
    <header
      className="w-full sticky top-0 z-40 backdrop-blur-sm header-with-fade"
      style={{ background: 'var(--bg)', paddingTop: 'env(safe-area-inset-top)' }}
    >
      <div className={`max-w-full mx-auto px-4 sm:px-6 lg:px-8 transition-all duration-500 ${
        showSearch ? 'h-auto py-4' : 'h-24'
      }`}>
        {/* When search is active: stack on mobile, side-by-side on desktop */}
        {showSearch && (
          <div className="flex flex-col lg:flex-row lg:justify-between lg:items-center gap-3">
            {/* Logo + Icon buttons - appear first on mobile (above search), last on desktop (right side) */}
            <div className="flex items-center justify-between w-full lg:w-auto lg:justify-end lg:order-2">
              {/* Logo - visible on mobile only, aligned left */}
              {logoUrl && (
                <img
                  src={logoUrl}
                  onClick={onLogoClick}
                  alt="Logo"
                  className="h-10 w-10 flex-shrink-0 cursor-pointer lg:hidden"
                />
              )}

              <IconButtons />
            </div>

            {/* Search bar - appear second on mobile (below logo+icons), first on desktop (left side) */}
            <div className="flex items-center gap-4 lg:order-1 flex-1">
              {/* Logo - visible on desktop only, aligned with search */}
              {logoUrl && (
                <img
                  src={logoUrl}
                  onClick={onLogoClick}
                  alt="Logo"
                  className="hidden lg:block h-12 w-12 flex-shrink-0 cursor-pointer"
                />
              )}
              <SearchBar
                ref={searchBarRef}
                className="flex-1 lg:flex-initial"
                inputClassName="lg:w-[50vw]"
                value={searchInput}
                onChange={handleSearchChange}
                onSubmit={handleHeaderSearch}
                onAdvancedToggle={onAdvancedToggle}
                isLoading={isLoading}
              />
            </div>
          </div>
        )}

        {/* When search is NOT active: show icon buttons only on the right */}
        {!showSearch && (
          <div className="flex items-center justify-end h-full">
            <IconButtons />
          </div>
        )}
      </div>
    </header>
  );
});

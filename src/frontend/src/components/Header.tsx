import { useState, useEffect } from 'react';

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
  statusCounts?: StatusCounts;
  onLogoClick?: () => void;
}

export const Header = ({ 
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
  statusCounts = { ongoing: 0, completed: 0, errored: 0 },
  onLogoClick,
}: HeaderProps) => {
  const [theme, setTheme] = useState<string>('auto');

  useEffect(() => {
    const saved = localStorage.getItem('preferred-theme') || 'auto';
    setTheme(saved);
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

  const applyTheme = (pref: string) => {
    if (pref === 'auto') {
      const isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
    } else {
      document.documentElement.setAttribute('data-theme', pref);
    }
  };

  const handleThemeChange = (newTheme: string) => {
    localStorage.setItem('preferred-theme', newTheme);
    setTheme(newTheme);
    applyTheme(newTheme);
  };

  const cycleTheme = () => {
    const themeOrder = ['light', 'dark', 'auto'];
    const currentIndex = themeOrder.indexOf(theme);
    const nextIndex = (currentIndex + 1) % themeOrder.length;
    handleThemeChange(themeOrder[nextIndex]);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && onSearch) {
      onSearch();
      (e.target as HTMLInputElement).blur();
    }
  };

  // Icon buttons component - reused for both states
  const IconButtons = () => (
    <div className="flex items-center gap-2">
      {/* Downloads Button */}
      {onDownloadsClick && (
        <button
          onClick={onDownloadsClick}
          className="relative p-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
          aria-label="View downloads"
          title="Downloads"
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
              d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3"
            />
          </svg>
          {/* Show badge with appropriate color based on status */}
          {(statusCounts.ongoing > 0 || statusCounts.completed > 0 || statusCounts.errored > 0) && (
            <span 
              className={`absolute -top-1 -right-1 text-white text-xs font-bold rounded-full w-5 h-5 flex items-center justify-center ${
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
        </button>
      )}

      {/* Theme Toggle Button */}
      <button
        onClick={cycleTheme}
        className="relative p-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
        aria-label={`Current theme: ${theme}. Click to cycle`}
        title={`Theme: ${theme.charAt(0).toUpperCase() + theme.slice(1)}`}
      >
        {theme === 'light' && (
          <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v2.25m6.364.386l-1.591 1.591M21 12h-2.25m-.386 6.364l-1.591-1.591M12 18.75V21m-4.773-4.227l-1.591 1.591M5.25 12H3m4.227-4.773L5.636 5.636M15.75 12a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0z" />
          </svg>
        )}
        {theme === 'dark' && (
          <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M21.752 15.002A9.718 9.718 0 0118 15.75c-5.385 0-9.75-4.365-9.75-9.75 0-1.33.266-2.597.748-3.752A9.753 9.753 0 003 11.25C3 16.635 7.365 21 12.75 21a9.753 9.753 0 009.002-5.998z" />
          </svg>
        )}
        {theme === 'auto' && (
          <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 17.25v1.007a3 3 0 01-.879 2.122L7.5 21h9l-.621-.621A3 3 0 0115 18.257V17.25m6-12V15a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 15V5.25m18 0A2.25 2.25 0 0018.75 3H5.25A2.25 2.25 0 003 5.25m18 0V12a2.25 2.25 0 01-2.25 2.25H5.25A2.25 2.25 0 013 12V5.25" />
          </svg>
        )}
      </button>

      {/* Calibre-Web Button */}
      {calibreWebUrl && (
        <a
          href={calibreWebUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 px-3 py-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
          aria-label="Open Calibre-Web"
          title="Go To Library"
        >
          <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 006 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 016 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 016-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0018 18a8.967 8.967 0 00-6 2.292m0-14.25v14.25" />
          </svg>
          <span className="text-sm font-medium">Go To Library</span>
        </a>
      )}

      {/* Debug Buttons */}
      {debug && (
        <>
          <form action="/request/debug" method="get">
            <button
              className="p-2 rounded-full bg-red-600/80 hover:bg-red-600 text-white transition-colors"
              type="submit"
              title="Debug"
            >
              <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 12.75c1.148 0 2.278.08 3.383.237 1.037.146 1.866.966 1.866 2.013 0 3.728-2.35 6.75-5.25 6.75S6.75 18.728 6.75 15c0-1.046.83-1.867 1.866-2.013A24.204 24.204 0 0112 12.75zm0 0c2.883 0 5.647.508 8.207 1.44a23.91 23.91 0 01-1.152 6.06M12 12.75c-2.883 0-5.647.508-8.208 1.44.125 2.104.52 4.136 1.153 6.06M12 12.75a2.25 2.25 0 002.248-2.354M12 12.75a2.25 2.25 0 01-2.248-2.354M12 8.25c.995 0 1.971-.08 2.922-.236.403-.066.74-.358.795-.762a3.778 3.778 0 00-.399-2.25M12 8.25c-.995 0-1.97-.08-2.922-.236-.402-.066-.74-.358-.795-.762a3.734 3.734 0 01.4-2.253M12 8.25a2.25 2.25 0 00-2.248 2.146M12 8.25a2.25 2.25 0 012.248 2.146M8.683 5a6.032 6.032 0 01-1.155-1.002c.07-.63.27-1.222.574-1.747m.581 2.749A3.75 3.75 0 0115.318 5m0 0c.427-.283.815-.62 1.155-.999a4.471 4.471 0 00-.575-1.752M4.921 6a24.048 24.048 0 00-.392 3.314c1.668.546 3.416.914 5.223 1.082M19.08 6c.205 1.08.337 2.187.392 3.314a23.882 23.882 0 01-5.223 1.082" />
              </svg>
            </button>
          </form>
          <form action="/request/api/restart" method="get">
            <button
              className="p-2 rounded-full bg-red-600 hover:bg-red-700 text-white transition-colors"
              type="submit"
              title="Restart"
            >
              <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
              </svg>
            </button>
          </form>
        </>
      )}
    </div>
  );

  return (
    <header className="w-full sticky top-0 z-40 backdrop-blur-sm header-with-fade" style={{ background: 'var(--bg)' }}>
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
              <div className="relative flex-1 lg:flex-initial">
                <input
                  type="search"
                  placeholder="Search by ISBN, title, author..."
                  aria-label="Search books"
                  className="w-full lg:w-[50vw] pl-4 pr-28 py-3 rounded-full border outline-none search-input"
                  style={{
                    background: 'var(--bg-soft)',
                    color: 'var(--text)',
                    borderColor: 'var(--border-muted)',
                  }}
                  value={searchInput}
                  onChange={(e) => onSearchChange?.(e.target.value)}
                  onKeyDown={handleKeyDown}
                />
                <div className="absolute inset-y-0 right-0 flex items-center gap-1 pr-2">
                  <button
                    type="button"
                    onClick={onAdvancedToggle}
                    className="p-2 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center justify-center transition-colors"
                    aria-label="Advanced Search"
                    title="Advanced Search"
                  >
                    <svg
                      className="w-5 h-5"
                      xmlns="http://www.w3.org/2000/svg"
                      fill="none"
                      viewBox="0 0 24 24"
                      strokeWidth="1.5"
                      stroke="currentColor"
                      style={{ color: 'var(--text)' }}
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M10.5 6h9.75M10.5 6a1.5 1.5 0 1 1-3 0m3 0a1.5 1.5 0 1 0-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 0 1-3 0m3 0a1.5 1.5 0 0 0-3 0m-9.75 0h9.75"
                      />
                    </svg>
                  </button>
                  <button
                    type="button"
                    onClick={onSearch}
                    className="p-2 rounded-full text-white bg-sky-700 hover:bg-sky-800 disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center transition-colors"
                    aria-label="Search books"
                    title="Search"
                    disabled={isLoading}
                  >
                    {!isLoading && (
                      <svg
                        className="w-5 h-5"
                        xmlns="http://www.w3.org/2000/svg"
                        fill="none"
                        viewBox="0 0 24 24"
                        strokeWidth="2"
                        stroke="currentColor"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z"
                        />
                      </svg>
                    )}
                    {isLoading && (
                      <div className="spinner w-3 h-3 border-2 border-white border-t-transparent" />
                    )}
                  </button>
                </div>
              </div>
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
};

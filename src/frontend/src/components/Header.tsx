import { useState, useEffect, useRef } from 'react';

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
}: HeaderProps) => {
  const [theme, setTheme] = useState<string>('auto');
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const saved = localStorage.getItem('preferred-theme') || 'auto';
    setTheme(saved);
    applyTheme(saved);
  }, []);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        menuRef.current &&
        buttonRef.current &&
        !menuRef.current.contains(e.target as Node) &&
        !buttonRef.current.contains(e.target as Node)
      ) {
        setMenuOpen(false);
      }
    };

    document.addEventListener('click', handleClickOutside);
    return () => document.removeEventListener('click', handleClickOutside);
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
    setMenuOpen(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && onSearch) {
      onSearch();
      (e.target as HTMLInputElement).blur();
    }
  };

  return (
    <header className="w-full">
      <div className={`max-w-full mx-auto px-4 sm:px-6 lg:px-8 transition-all duration-500 ${
        showSearch ? 'h-auto py-4' : 'h-24'
      } flex ${
        showSearch ? 'flex-col lg:flex-row lg:justify-between lg:items-center gap-4' : 'flex-row items-center justify-end'
      }`}>
        {/* Logo and compact search bar - fade in when search is active */}
        <div className={`flex items-center gap-8 pl-5 transition-all duration-500 ${
          showSearch 
            ? 'opacity-100 w-full lg:w-auto' 
            : 'opacity-0 pointer-events-none absolute'
        }`}>
          {logoUrl && (
            <img src={logoUrl} onClick={() => window.location.href = calibreWebUrl || ''} alt="Logo" className="hidden min-[400px]:block h-12 w-12 flex-shrink-0" />
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
                  <div
                    className="spinner w-3 h-3 border-2 border-white border-t-transparent"
                  />
                )}
              </button>
            </div>
          </div>
        </div>
        
        {/* Theme and action buttons */}
        <div className="flex items-center gap-2">
          {/* Theme Dropdown */}
          <div className="relative">
            <button
              ref={buttonRef}
              onClick={() => setMenuOpen(!menuOpen)}
              className="px-3 py-1 rounded border text-sm hover:opacity-80"
              style={{ borderColor: 'var(--border-muted)' }}
            >
              Theme ({theme})
            </button>
            <div
              ref={menuRef}
              className={`absolute right-0 mt-2 w-36 rounded-md shadow-lg ring-1 ring-black/5 z-50 ${
                menuOpen ? '' : 'hidden'
              }`}
              style={{ background: 'var(--bg-soft)' }}
            >
              <ul className="py-1 text-sm">
                <li>
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault();
                      handleThemeChange('light');
                    }}
                    className="block px-3 py-1 hover:bg-black/10"
                    style={{ color: 'var(--text)' }}
                  >
                    Light
                  </a>
                </li>
                <li>
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault();
                      handleThemeChange('dark');
                    }}
                    className="block px-3 py-1 hover:bg-black/10"
                    style={{ color: 'var(--text)' }}
                  >
                    Dark
                  </a>
                </li>
                <li>
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault();
                      handleThemeChange('auto');
                    }}
                    className="block px-3 py-1 hover:bg-black/10"
                    style={{ color: 'var(--text)' }}
                  >
                    Auto (System)
                  </a>
                </li>
              </ul>
            </div>
          </div>

          {calibreWebUrl && (
            <a
              href={calibreWebUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="px-3 py-1 rounded border text-sm hover:opacity-80"
              style={{ borderColor: 'var(--border-muted)' }}
            >
              Calibre-Web
            </a>
          )}

          {debug && (
            <>
              <form action="/request/debug" method="get">
                <button
                  className="px-3 py-1 rounded bg-red-600/80 text-white text-sm hover:bg-red-600"
                  type="submit"
                >
                  DEBUG
                </button>
              </form>
              <form action="/request/api/restart" method="get">
                <button
                  className="px-3 py-1 rounded bg-red-600 text-white text-sm hover:bg-red-700"
                  type="submit"
                >
                  RESTART
                </button>
              </form>
            </>
          )}
        </div>
      </div>
    </header>
  );
};

import { useState, useEffect, useRef } from 'react';

interface HeaderProps {
  calibreWebUrl?: string;
  debug?: boolean;
}

export const Header = ({ calibreWebUrl, debug }: HeaderProps) => {
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

  return (
    <header className="w-full">
      <div className="max-w-full mx-auto px-4 sm:px-6 lg:px-8 h-12 flex items-center justify-end">
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

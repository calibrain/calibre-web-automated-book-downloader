import { useState } from 'react';
import { Language } from '../types';

interface SearchSectionProps {
  onSearch: (query: string) => void;
  isLoading: boolean;
  isInitialState: boolean;
  bookLanguages: Language[];
  defaultLanguage: string;
  supportedFormats: string[];
  logoUrl: string;
  searchInput: string;
  onSearchInputChange: (value: string) => void;
  showAdvanced: boolean;
  onAdvancedToggle: () => void;
}

export const SearchSection = ({
  onSearch,
  isLoading,
  isInitialState,
  bookLanguages,
  defaultLanguage,
  supportedFormats,
  logoUrl,
  searchInput,
  onSearchInputChange,
  showAdvanced,
  onAdvancedToggle,
}: SearchSectionProps) => {
  const [isbn, setIsbn] = useState('');
  const [author, setAuthor] = useState('');
  const [title, setTitle] = useState('');
  const [lang, setLang] = useState(defaultLanguage || 'all');
  const [sort, setSort] = useState('');
  const [content, setContent] = useState('');
  const [formats, setFormats] = useState<string[]>(
    supportedFormats.filter(f => f !== 'pdf')
  );

  const buildQuery = () => {
    const q: string[] = [];
    const basic = searchInput.trim();
    if (basic) q.push(`query=${encodeURIComponent(basic)}`);

    if (!showAdvanced) return q.join('&');

    if (isbn) q.push(`isbn=${encodeURIComponent(isbn)}`);
    if (author) q.push(`author=${encodeURIComponent(author)}`);
    if (title) q.push(`title=${encodeURIComponent(title)}`);
    if (lang && lang !== 'all') q.push(`lang=${encodeURIComponent(lang)}`);
    if (sort) q.push(`sort=${encodeURIComponent(sort)}`);
    if (content) q.push(`content=${encodeURIComponent(content)}`);
    formats.forEach(f => q.push(`format=${encodeURIComponent(f)}`));

    return q.join('&');
  };

  const handleSearch = () => {
    const query = buildQuery();
    onSearch(query);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch();
      (e.target as HTMLInputElement).blur();
    }
  };

  const toggleFormat = (format: string) => {
    setFormats(prev =>
      prev.includes(format) ? prev.filter(f => f !== format) : [...prev, format]
    );
  };

  return (
    <section
      id="search-section"
      className={`transition-all duration-500 ease-in-out ${
        isInitialState 
          ? 'search-initial-state mb-6' 
          : 'mb-0'
      }`}
    >
      <div className={`flex items-center justify-center gap-3 mb-8 transition-all duration-300 ${
        isInitialState ? 'opacity-100' : 'opacity-0 h-0 mb-0 overflow-hidden'
      }`}>
        <img src={logoUrl} alt="Logo" className="h-8 w-8" />
        <h1 className="text-2xl font-semibold">Book Search & Download</h1>
      </div>
      <div className={`flex flex-col gap-3 search-wrapper transition-all duration-500 ${
        isInitialState ? '' : 'hidden'
      }`}>
        <div className="relative">
          <input
            type="search"
            placeholder="Search by ISBN, title, author..."
            aria-label="Search books"
            className="w-full pl-4 pr-28 py-3 rounded-full border outline-none search-input"
            style={{
              background: 'var(--bg-soft)',
              color: 'var(--text)',
              borderColor: 'var(--border-muted)',
            }}
            value={searchInput}
            onChange={e => onSearchInputChange(e.target.value)}
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
              onClick={handleSearch}
              className="p-2 rounded-full text-white bg-sky-700 hover:bg-sky-800 disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center transition-colors"
              aria-label="Search books"
              title="Search"
              disabled={isLoading}
            >
              {!isLoading && (
                <svg
                  id="search-icon"
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
                  id="search-spinner"
                  className="spinner w-3 h-3 border-2 border-white border-t-transparent"
                />
              )}
            </button>
          </div>
        </div>
        {/* Advanced Filters */}
        <form
          id="search-filters"
          className={`grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 ${
            showAdvanced ? '' : 'hidden'
          }`}
        >
          <div>
            <label htmlFor="isbn-input" className="block text-sm mb-1 opacity-80">
              ISBN
            </label>
            <input
              id="isbn-input"
              type="search"
              placeholder="ISBN"
              className="w-full px-3 py-2 rounded-md border"
              style={{
                background: 'var(--bg-soft)',
                color: 'var(--text)',
                borderColor: 'var(--border-muted)',
              }}
              value={isbn}
              onChange={e => setIsbn(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="author-input" className="block text-sm mb-1 opacity-80">
              Author
            </label>
            <input
              id="author-input"
              type="search"
              placeholder="Author"
              className="w-full px-3 py-2 rounded-md border"
              style={{
                background: 'var(--bg-soft)',
                color: 'var(--text)',
                borderColor: 'var(--border-muted)',
              }}
              value={author}
              onChange={e => setAuthor(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="title-input" className="block text-sm mb-1 opacity-80">
              Title
            </label>
            <input
              id="title-input"
              type="search"
              placeholder="Title"
              className="w-full px-3 py-2 rounded-md border"
              style={{
                background: 'var(--bg-soft)',
                color: 'var(--text)',
                borderColor: 'var(--border-muted)',
              }}
              value={title}
              onChange={e => setTitle(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="lang-input" className="block text-sm mb-1 opacity-80">
              Language
            </label>
            <select
              id="lang-input"
              className="w-full px-3 py-2 rounded-md border"
              style={{
                background: 'var(--bg-soft)',
                color: 'var(--text)',
                borderColor: 'var(--border-muted)',
              }}
              value={lang}
              onChange={e => setLang(e.target.value)}
            >
              <option value="all">All</option>
              {bookLanguages.map(l => (
                <option key={l.code} value={l.code}>
                  {l.language}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="sort-input" className="block text-sm mb-1 opacity-80">
              Sort
            </label>
            <select
              id="sort-input"
              className="w-full px-3 py-2 rounded-md border"
              style={{
                background: 'var(--bg-soft)',
                color: 'var(--text)',
                borderColor: 'var(--border-muted)',
              }}
              value={sort}
              onChange={e => setSort(e.target.value)}
            >
              <option value="">Most relevant</option>
              <option value="newest">Newest (publication year)</option>
              <option value="oldest">Oldest (publication year)</option>
              <option value="largest">Largest (filesize)</option>
              <option value="smallest">Smallest (filesize)</option>
              <option value="newest_added">Newest (open sourced)</option>
              <option value="oldest_added">Oldest (open sourced)</option>
            </select>
          </div>
          <div>
            <label htmlFor="content-input" className="block text-sm mb-1 opacity-80">
              Content
            </label>
            <select
              id="content-input"
              className="w-full px-3 py-2 rounded-md border"
              style={{
                background: 'var(--bg-soft)',
                color: 'var(--text)',
                borderColor: 'var(--border-muted)',
              }}
              value={content}
              onChange={e => setContent(e.target.value)}
            >
              <option value="">All</option>
              <option value="book_nonfiction">Book (non-fiction)</option>
              <option value="book_fiction">Book (fiction)</option>
              <option value="book_unknown">Book (unknown)</option>
              <option value="magazine">Magazine</option>
              <option value="book_comic">Comic Book</option>
              <option value="standards_document">Standards document</option>
              <option value="other">Other</option>
              <option value="musical_score">Musical score</option>
              <option value="audiobook">Audiobook</option>
            </select>
          </div>
          <div className="md:col-span-2 lg:col-span-3">
            <label className="block text-sm mb-1 opacity-80">Formats</label>
            <div className="flex flex-wrap gap-3 text-sm">
              {['pdf', 'epub', 'mobi', 'azw3', 'fb2', 'djvu', 'cbz', 'cbr'].map(format => {
                const isSupported = supportedFormats.includes(format);
                return (
                  <label
                    key={format}
                    className={`inline-flex items-center gap-2 ${
                      !isSupported ? 'opacity-50 cursor-not-allowed' : ''
                    }`}
                  >
                    <input
                      type="checkbox"
                      value={format}
                      checked={formats.includes(format)}
                      onChange={() => toggleFormat(format)}
                      disabled={!isSupported}
                    />
                    {format.toUpperCase()}
                  </label>
                );
              })}
            </div>
          </div>
        </form>
      </div>
    </section>
  );
};

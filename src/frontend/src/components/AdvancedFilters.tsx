import { useState } from 'react';
import { Language } from '../types';

interface AdvancedFiltersProps {
  visible: boolean;
  bookLanguages: Language[];
  defaultLanguage: string;
  supportedFormats: string[];
  onFiltersChange: (filters: {
    isbn: string;
    author: string;
    title: string;
    lang: string;
    sort: string;
    content: string;
    formats: string[];
  }) => void;
}

export const AdvancedFilters = ({
  visible,
  bookLanguages,
  defaultLanguage,
  supportedFormats,
  onFiltersChange,
}: AdvancedFiltersProps) => {
  const [isbn, setIsbn] = useState('');
  const [author, setAuthor] = useState('');
  const [title, setTitle] = useState('');
  const [lang, setLang] = useState(defaultLanguage || 'all');
  const [sort, setSort] = useState('');
  const [content, setContent] = useState('');
  const [formats, setFormats] = useState<string[]>(
    supportedFormats.filter(f => f !== 'pdf')
  );

  const notifyChange = (updates?: Partial<{
    isbn: string;
    author: string;
    title: string;
    lang: string;
    sort: string;
    content: string;
    formats: string[];
  }>) => {
    onFiltersChange({
      isbn,
      author,
      title,
      lang,
      sort,
      content,
      formats,
      ...updates,
    });
  };

  const toggleFormat = (format: string) => {
    const newFormats = formats.includes(format)
      ? formats.filter(f => f !== format)
      : [...formats, format];
    setFormats(newFormats);
    notifyChange({ formats: newFormats });
  };

  if (!visible) return null;

  return (
    <div className="w-full border-b pb-4 mb-4" style={{ borderColor: 'var(--border-muted)' }}>
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <form
          id="search-filters"
          className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
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
              onChange={e => {
                setIsbn(e.target.value);
                notifyChange({ isbn: e.target.value });
              }}
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
              onChange={e => {
                setAuthor(e.target.value);
                notifyChange({ author: e.target.value });
              }}
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
              onChange={e => {
                setTitle(e.target.value);
                notifyChange({ title: e.target.value });
              }}
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
              onChange={e => {
                setLang(e.target.value);
                notifyChange({ lang: e.target.value });
              }}
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
              onChange={e => {
                setSort(e.target.value);
                notifyChange({ sort: e.target.value });
              }}
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
              onChange={e => {
                setContent(e.target.value);
                notifyChange({ content: e.target.value });
              }}
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
    </div>
  );
};

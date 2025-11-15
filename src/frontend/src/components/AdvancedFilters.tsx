import { AdvancedFilterState, Language } from '../types';
import { normalizeLanguageSelection } from '../utils/languageFilters';
import { LanguageMultiSelect } from './LanguageMultiSelect';
import { DropdownList } from './DropdownList';
import { CONTENT_OPTIONS } from '../data/filterOptions';

interface AdvancedFiltersProps {
  visible: boolean;
  bookLanguages: Language[];
  defaultLanguage: string[];
  supportedFormats: string[];
  filters: AdvancedFilterState;
  onFiltersChange: (updates: Partial<AdvancedFilterState>) => void;
}

export const AdvancedFilters = ({
  visible,
  bookLanguages,
  defaultLanguage,
  supportedFormats,
  filters,
  onFiltersChange,
}: AdvancedFiltersProps) => {
  const { isbn, author, title, lang, content, formats } = filters;

  const handleLangChange = (next: string[]) => {
    const normalized = normalizeLanguageSelection(next);
    onFiltersChange({ lang: normalized });
  };

  const handleContentChange = (next: string[] | string) => {
    const value = Array.isArray(next) ? next[0] ?? '' : next;
    onFiltersChange({ content: value });
  };

  const toggleFormat = (format: string) => {
    const newFormats = formats.includes(format)
      ? formats.filter(f => f !== format)
      : [...formats, format];
    onFiltersChange({ formats: newFormats });
  };

  if (!visible) return null;

  return (
    <div className="w-full border-b pt-6 pb-4 mb-4" style={{ borderColor: 'var(--border-muted)' }}>
      <div className="w-full px-4 sm:px-6 lg:px-8">
        <form
          id="search-filters"
          className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 px-2 lg:ml-[calc(3rem+1rem)] lg:w-[50vw]"
        >
          <div>
            <label htmlFor="isbn-input" className="block text-sm mb-1 opacity-80">
              ISBN
            </label>
            <input
              id="isbn-input"
              type="text"
              placeholder="ISBN"
              autoComplete="off"
              className="w-full px-3 py-2 rounded-md border"
              style={{
                background: 'var(--bg-soft)',
                color: 'var(--text)',
                borderColor: 'var(--border-muted)',
              }}
              value={isbn}
              onChange={e => {
                onFiltersChange({ isbn: e.target.value });
              }}
            />
          </div>
          <div>
            <label htmlFor="author-input" className="block text-sm mb-1 opacity-80">
              Author
            </label>
            <input
              id="author-input"
              type="text"
              placeholder="Author"
              autoComplete="off"
              className="w-full px-3 py-2 rounded-md border"
              style={{
                background: 'var(--bg-soft)',
                color: 'var(--text)',
                borderColor: 'var(--border-muted)',
              }}
              value={author}
              onChange={e => {
                onFiltersChange({ author: e.target.value });
              }}
            />
          </div>
          <div>
            <label htmlFor="title-input" className="block text-sm mb-1 opacity-80">
              Title
            </label>
            <input
              id="title-input"
              type="text"
              placeholder="Title"
              autoComplete="off"
              className="w-full px-3 py-2 rounded-md border"
              style={{
                background: 'var(--bg-soft)',
                color: 'var(--text)',
                borderColor: 'var(--border-muted)',
              }}
              value={title}
              onChange={e => {
                onFiltersChange({ title: e.target.value });
              }}
            />
          </div>
          <LanguageMultiSelect
            options={bookLanguages}
            value={lang}
            onChange={handleLangChange}
            defaultLanguageCodes={defaultLanguage}
            label="Language"
          />
          <DropdownList
            label="Content"
            options={CONTENT_OPTIONS}
            value={content}
            onChange={handleContentChange}
            placeholder="All"
          />
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

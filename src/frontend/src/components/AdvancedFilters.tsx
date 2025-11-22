import { ReactNode } from 'react';
import { AdvancedFilterState, Language } from '../types';
import { normalizeLanguageSelection } from '../utils/languageFilters';
import { LanguageMultiSelect } from './LanguageMultiSelect';
import { DropdownList } from './DropdownList';
import { CONTENT_OPTIONS } from '../data/filterOptions';

const FORMAT_TYPES = ['pdf', 'epub', 'mobi', 'azw3', 'fb2', 'djvu', 'cbz', 'cbr'] as const;

interface AdvancedFiltersProps {
  visible: boolean;
  bookLanguages: Language[];
  defaultLanguage: string[];
  supportedFormats: string[];
  filters: AdvancedFilterState;
  onFiltersChange: (updates: Partial<AdvancedFilterState>) => void;
  formClassName?: string;
  renderWrapper?: (form: ReactNode) => ReactNode;
}

export const AdvancedFilters = ({
  visible,
  bookLanguages,
  defaultLanguage,
  supportedFormats,
  filters,
  onFiltersChange,
  formClassName,
  renderWrapper,
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

  const handleFormatsChange = (next: string[] | string) => {
    const nextFormats = Array.isArray(next) ? next : next ? [next] : [];
    onFiltersChange({ formats: nextFormats });
  };

  const formatOptions = FORMAT_TYPES.map(format => ({
    value: format,
    label: format.toUpperCase(),
    disabled: !supportedFormats.includes(format),
  }));

  if (!visible) return null;

  const form = (
    <form
      id="search-filters"
      className={
        formClassName ??
        'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 px-2 lg:ml-[calc(3rem+1rem)] lg:w-[50vw]'
      }
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
          <div>
            <DropdownList
              label="Formats"
              placeholder="Any"
              options={formatOptions}
              value={formats}
              onChange={handleFormatsChange}
              multiple
              showCheckboxes
              keepOpenOnSelect
            />
          </div>
    </form>
  );

  const wrappedForm = renderWrapper ? (
    renderWrapper(form)
  ) : (
    <div className="w-full border-b pt-6 pb-4 mb-4" style={{ borderColor: 'var(--border-muted)' }}>
      <div className="w-full px-4 sm:px-6 lg:px-8">{form}</div>
    </div>
  );

  return wrappedForm;
};

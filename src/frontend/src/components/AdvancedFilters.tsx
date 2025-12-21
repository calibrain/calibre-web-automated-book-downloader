import { ReactNode, KeyboardEvent } from 'react';
import { AdvancedFilterState, Language, MetadataSearchField } from '../types';
import { normalizeLanguageSelection } from '../utils/languageFilters';
import { useSearchMode } from '../contexts/SearchModeContext';
import { LanguageMultiSelect } from './LanguageMultiSelect';
import { DropdownList } from './DropdownList';
import { CONTENT_OPTIONS } from '../data/filterOptions';
import { SearchFieldRenderer } from './shared';

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
  // Universal mode props
  metadataSearchFields?: MetadataSearchField[];
  searchFieldValues?: Record<string, string | number | boolean>;
  onSearchFieldChange?: (key: string, value: string | number | boolean) => void;
  // Submit handler for Enter key
  onSubmit?: () => void;
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
  metadataSearchFields = [],
  searchFieldValues = {},
  onSearchFieldChange,
  onSubmit,
}: AdvancedFiltersProps) => {
  const { searchMode } = useSearchMode();
  const { isbn, author, title, lang, content, formats } = filters;

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && onSubmit) {
      e.preventDefault();
      onSubmit();
    }
  };

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

  // Universal search mode: render dynamic provider fields
  if (searchMode === 'universal') {
    // If no fields defined for this provider, don't show the section
    if (metadataSearchFields.length === 0) return null;

    const universalForm = (
      <form
        id="search-filters"
        className={
          formClassName ??
          'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 px-2 lg:ml-[calc(3rem+1rem)] lg:w-[50vw]'
        }
      >
        {metadataSearchFields.map((field) => (
          <div key={field.key}>
            {field.type !== 'CheckboxSearchField' && (
              <label htmlFor={`${field.key}-input`} className="block text-sm mb-1 opacity-80">
                {field.label}
              </label>
            )}
            <SearchFieldRenderer
              field={field}
              value={searchFieldValues[field.key] ?? (field.type === 'CheckboxSearchField' ? false : '')}
              onChange={(value) => onSearchFieldChange?.(field.key, value)}
              onSubmit={onSubmit}
            />
            {field.description && (
              <p className="text-xs mt-1 opacity-60">{field.description}</p>
            )}
          </div>
        ))}
      </form>
    );

    const wrappedUniversalForm = renderWrapper ? (
      renderWrapper(universalForm)
    ) : (
      <div className="w-full border-b pt-6 pb-4 mb-4" style={{ borderColor: 'var(--border-muted)' }}>
        <div className="w-full px-4 sm:px-6 lg:px-8">{universalForm}</div>
      </div>
    );

    return wrappedUniversalForm;
  }

  // Direct download mode: render existing hardcoded filters
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
              enterKeyHint="search"
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
              onKeyDown={handleKeyDown}
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
              enterKeyHint="search"
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
              onKeyDown={handleKeyDown}
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
              enterKeyHint="search"
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
              onKeyDown={handleKeyDown}
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

import type { ReactNode } from 'react';

import { CONTENT_OPTIONS } from '../data/filterOptions';
import { t } from '../i18n';
import type {
  AdvancedFilterState,
  ContentType,
  Language,
  MetadataProviderSummary,
  SearchMode,
} from '../types';
import { normalizeLanguageSelection } from '../utils/languageFilters';
import { DropdownList } from './DropdownList';
import { LanguageMultiSelect } from './LanguageMultiSelect';

const FORMAT_TYPES = [
  'pdf',
  'epub',
  'mobi',
  'azw3',
  'fb2',
  'djvu',
  'cbz',
  'cbr',
  'zip',
  'rar',
] as const;

interface AdvancedFiltersProps {
  visible: boolean;
  bookLanguages: Language[];
  defaultLanguage: string[];
  filters: AdvancedFilterState;
  onFiltersChange: (updates: Partial<AdvancedFilterState>) => void;
  formClassName?: string;
  renderWrapper?: (form: ReactNode) => ReactNode;
  searchMode: SearchMode;
  onSearchModeChange: (mode: SearchMode) => void;
  metadataProviders?: MetadataProviderSummary[];
  activeMetadataProvider?: string | null;
  onMetadataProviderChange?: (provider: string) => void;
  contentType?: ContentType;
  combinedMode?: boolean;
  isAdmin?: boolean;
  onClose?: () => void;
}

const SEARCH_MODE_OPTIONS = [
  {
    value: 'direct',
    label: t('Direct'),
    description: t('Search web sources for books and download directly. Works out of the box.'),
  },
  {
    value: 'universal',
    label: 'Universal',
    description: t(
      'Metadata-based search with downloads from all sources. Book and Audiobook support.',
    ),
  },
];

const EMPTY_PROVIDERS: MetadataProviderSummary[] = [];

export const AdvancedFilters = ({
  visible,
  bookLanguages,
  defaultLanguage,
  filters,
  onFiltersChange,
  formClassName,
  renderWrapper,
  searchMode,
  onSearchModeChange,
  metadataProviders = EMPTY_PROVIDERS,
  activeMetadataProvider,
  onMetadataProviderChange,
  contentType = 'ebook',
  combinedMode = false,
  isAdmin = false,
  onClose,
}: AdvancedFiltersProps) => {
  const { lang, content, formats } = filters;

  const handleLangChange = (next: string[]) => {
    const normalized = normalizeLanguageSelection(next);
    onFiltersChange({ lang: normalized });
  };

  const handleContentChange = (next: string[] | string) => {
    const value = Array.isArray(next) ? (next[0] ?? '') : next;
    onFiltersChange({ content: value });
  };

  const handleFormatsChange = (next: string[] | string) => {
    let nextFormats: string[] = [];
    if (Array.isArray(next)) {
      nextFormats = next;
    } else if (next) {
      nextFormats = [next];
    }
    onFiltersChange({ formats: nextFormats });
  };

  const formatOptions = FORMAT_TYPES.map((format) => ({
    value: format,
    label: format.toUpperCase(),
  }));

  const providerOptions = metadataProviders.map((provider) => {
    const details: string[] = [];
    if (!provider.enabled) details.push(t('Disabled in Settings'));
    if (provider.enabled && !provider.available) details.push(t('Not configured'));
    if (provider.requires_auth) details.push(t('API key required'));

    return {
      value: provider.name,
      label: provider.display_name,
      description: details.length > 0 ? details.join(' • ') : undefined,
      disabled: !provider.enabled || !provider.available,
    };
  });

  let metadataProviderLabel = t('Book Metadata Provider');
  if (combinedMode) {
    metadataProviderLabel = t('Combined Metadata Provider');
  } else if (contentType === 'audiobook') {
    metadataProviderLabel = t('Audiobook Metadata Provider');
  }

  if (!visible) return null;

  const wrapperClassName = formClassName ? 'px-2' : 'px-2 lg:ml-16 lg:w-[calc(50vw+4rem)]';

  const settingsForm = (
    <div className={wrapperClassName} role="presentation" onMouseDown={(e) => e.stopPropagation()}>
      {onClose && (
        <div className="mb-1 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="hover-action rounded-full p-1 transition-colors"
            aria-label={t('Close filters')}
            title={t('Close filters')}
          >
            <svg
              className="h-4 w-4"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth="2"
              stroke="currentColor"
              style={{ color: 'var(--text-muted)' }}
              aria-hidden="true"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}
      {isAdmin && (
        <div className="mb-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          <DropdownList
            label={t('Search Mode')}
            options={SEARCH_MODE_OPTIONS}
            value={searchMode}
            onChange={(value) => {
              const next = Array.isArray(value) ? (value[0] ?? 'direct') : value;
              onSearchModeChange(next === 'universal' ? 'universal' : 'direct');
            }}
            placeholder={t('Choose a mode')}
            widthClassName="w-full"
          />

          {searchMode === 'universal' && (
            <DropdownList
              label={metadataProviderLabel}
              options={providerOptions}
              value={activeMetadataProvider ?? ''}
              onChange={(value) => {
                const next = Array.isArray(value) ? (value[0] ?? '') : value;
                onMetadataProviderChange?.(next);
              }}
              placeholder={t('Choose a provider')}
              widthClassName="w-full"
            />
          )}
        </div>
      )}

      {searchMode === 'direct' && (
        <div className="space-y-4">
          <form
            id="search-filters"
            className={formClassName ?? 'grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3'}
          >
            <LanguageMultiSelect
              options={bookLanguages}
              value={lang}
              onChange={handleLangChange}
              defaultLanguageCodes={defaultLanguage}
              label={t('Language')}
            />
            <DropdownList
              label={t('Content')}
              options={CONTENT_OPTIONS}
              value={content}
              onChange={handleContentChange}
              placeholder={t('All')}
            />
            <DropdownList
              label={t('Formats')}
              placeholder={t('Any')}
              options={formatOptions}
              value={formats}
              onChange={handleFormatsChange}
              multiple
              showCheckboxes
              keepOpenOnSelect
            />
          </form>
        </div>
      )}
    </div>
  );

  return renderWrapper ? (
    renderWrapper(settingsForm)
  ) : (
    <div className="mb-4 w-full border-b pt-6 pb-4" style={{ borderColor: 'var(--border-muted)' }}>
      <div className="w-full px-4 sm:px-6 lg:px-8">{settingsForm}</div>
    </div>
  );
};

import { t } from '../i18n';
import type {
  AdvancedFilterState,
  Language,
  MetadataSearchField,
  ContentType,
  QueryTargetOption,
  SearchMode,
  MetadataProviderSummary,
} from '../types';
import { AdvancedFilters } from './AdvancedFilters';
import { SearchBar } from './SearchBar';

interface SearchSectionProps {
  onSearch: () => void;
  isLoading: boolean;
  isInitialState: boolean;
  searchPageTitle: string;
  bookLanguages: Language[];
  defaultLanguage: string[];
  logoUrl: string;
  queryValue: string | number | boolean;
  queryValueLabel?: string;
  onQueryValueChange: (value: string | number | boolean, label?: string) => void;
  queryTargets: QueryTargetOption[];
  activeQueryTarget: string;
  onQueryTargetChange: (key: string) => void;
  showAdvanced: boolean;
  onAdvancedToggle?: () => void;
  advancedFilters: AdvancedFilterState;
  onAdvancedFiltersChange: (updates: Partial<AdvancedFilterState>) => void;
  contentType?: ContentType;
  onContentTypeChange?: (type: ContentType) => void;
  allowedContentTypes?: ContentType[];
  combinedMode?: boolean;
  combinedModeLocked?: boolean;
  onCombinedModeChange?: (enabled: boolean) => void;
  activeQueryField?: MetadataSearchField | null;
  searchMode: SearchMode;
  onSearchModeChange: (mode: SearchMode) => void;
  metadataProviders?: MetadataProviderSummary[];
  activeMetadataProvider?: string | null;
  onMetadataProviderChange?: (provider: string) => void;
  isAdmin?: boolean;
}

export const SearchSection = ({
  onSearch,
  isLoading,
  isInitialState,
  searchPageTitle,
  bookLanguages,
  defaultLanguage,
  logoUrl,
  queryValue,
  queryValueLabel,
  onQueryValueChange,
  queryTargets,
  activeQueryTarget,
  onQueryTargetChange,
  showAdvanced,
  onAdvancedToggle,
  advancedFilters,
  onAdvancedFiltersChange,
  contentType = 'ebook',
  onContentTypeChange,
  allowedContentTypes,
  combinedMode,
  combinedModeLocked,
  onCombinedModeChange,
  activeQueryField,
  searchMode,
  onSearchModeChange,
  metadataProviders,
  activeMetadataProvider,
  onMetadataProviderChange,
  isAdmin = false,
}: SearchSectionProps) => {
  return (
    <section
      id="search-section"
      className={`${
        isInitialState ? 'search-initial-state mb-6' : 'mb-3 sm:mb-4'
      } ${showAdvanced ? 'search-advanced-visible' : ''}`}
    >
      <div
        className={`flex items-center justify-center gap-3 transition-all duration-300 ${
          isInitialState ? 'mb-6 opacity-100 sm:mb-8' : 'mb-0 h-0 overflow-hidden opacity-0'
        }`}
      >
        <img src={logoUrl} alt={t('Logo')} className="h-8 w-8" />
        <h1 className="text-2xl font-semibold">{searchPageTitle}</h1>
      </div>
      <div
        className={`search-wrapper flex flex-col gap-3 transition-all duration-500 ${
          isInitialState ? '' : 'hidden'
        }`}
      >
        <SearchBar
          value={queryValue}
          valueLabel={queryValueLabel}
          onChange={onQueryValueChange}
          onSubmit={onSearch}
          isLoading={isLoading}
          onAdvancedToggle={onAdvancedToggle}
          isAdvancedActive={showAdvanced}
          contentType={contentType}
          onContentTypeChange={onContentTypeChange}
          allowedContentTypes={allowedContentTypes}
          combinedMode={combinedMode}
          combinedModeLocked={combinedModeLocked}
          onCombinedModeChange={onCombinedModeChange}
          queryTargets={queryTargets}
          activeQueryTarget={activeQueryTarget}
          onQueryTargetChange={onQueryTargetChange}
          activeQueryField={activeQueryField}
          optionsLayout="inline"
        />
        {activeQueryTarget === 'manual' && (
          <p className="px-2 text-xs opacity-50">
            {t(
              'Manual search queries release sources directly. Some sources may return limited metadata, which can affect file naming templates.',
            )}
          </p>
        )}
        <AdvancedFilters
          visible={showAdvanced}
          bookLanguages={bookLanguages}
          defaultLanguage={defaultLanguage}
          filters={advancedFilters}
          onFiltersChange={onAdvancedFiltersChange}
          formClassName="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4"
          renderWrapper={(form) => form}
          searchMode={searchMode}
          onSearchModeChange={onSearchModeChange}
          metadataProviders={metadataProviders}
          activeMetadataProvider={activeMetadataProvider}
          onMetadataProviderChange={onMetadataProviderChange}
          contentType={contentType}
          combinedMode={combinedMode}
          isAdmin={isAdmin}
          onClose={onAdvancedToggle}
        />
      </div>
    </section>
  );
};

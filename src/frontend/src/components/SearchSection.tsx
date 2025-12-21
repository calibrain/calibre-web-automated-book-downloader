import { AdvancedFilterState, Language, MetadataSearchField } from '../types';
import { buildSearchQuery } from '../utils/buildSearchQuery';
import { useSearchMode } from '../contexts/SearchModeContext';
import { AdvancedFilters } from './AdvancedFilters';
import { SearchBar } from './SearchBar';

interface SearchSectionProps {
  onSearch: (query: string) => void;
  isLoading: boolean;
  isInitialState: boolean;
  bookLanguages: Language[];
  defaultLanguage: string[];
  supportedFormats: string[];
  logoUrl: string;
  searchInput: string;
  onSearchInputChange: (value: string) => void;
  showAdvanced: boolean;
  onAdvancedToggle: () => void;
  advancedFilters: AdvancedFilterState;
  onAdvancedFiltersChange: (updates: Partial<AdvancedFilterState>) => void;
  // Universal mode props
  metadataSearchFields?: MetadataSearchField[];
  searchFieldValues?: Record<string, string | number | boolean>;
  onSearchFieldChange?: (key: string, value: string | number | boolean) => void;
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
  advancedFilters,
  onAdvancedFiltersChange,
  metadataSearchFields,
  searchFieldValues,
  onSearchFieldChange,
}: SearchSectionProps) => {
  const { searchMode } = useSearchMode();

  const handleSearch = () => {
    const query = buildSearchQuery({
      searchInput,
      showAdvanced,
      advancedFilters,
      bookLanguages,
      defaultLanguage,
      searchMode,
    });
    onSearch(query);
  };

  return (
    <section
      id="search-section"
      className={`transition-all duration-500 ease-in-out ${
        isInitialState 
          ? 'search-initial-state mb-6' 
          : 'mb-3 sm:mb-4'
      } ${showAdvanced ? 'search-advanced-visible' : ''}`}
    >
      <div className={`flex items-center justify-center gap-3 transition-all duration-300 ${
        isInitialState ? 'opacity-100 mb-6 sm:mb-8' : 'opacity-0 h-0 mb-0 overflow-hidden'
      }`}>
        <img src={logoUrl} alt="Logo" className="h-8 w-8" />
        <h1 className="text-2xl font-semibold">Book Search & Download</h1>
      </div>
      <div className={`flex flex-col gap-3 search-wrapper transition-all duration-500 ${
        isInitialState ? '' : 'hidden'
      }`}>
        <SearchBar
          value={searchInput}
          onChange={onSearchInputChange}
          onSubmit={handleSearch}
          isLoading={isLoading}
          onAdvancedToggle={onAdvancedToggle}
        />
        <AdvancedFilters
          visible={showAdvanced}
          bookLanguages={bookLanguages}
          defaultLanguage={defaultLanguage}
          supportedFormats={supportedFormats}
          filters={advancedFilters}
          onFiltersChange={onAdvancedFiltersChange}
          formClassName="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 px-2"
          renderWrapper={form => form}
          metadataSearchFields={metadataSearchFields}
          searchFieldValues={searchFieldValues}
          onSearchFieldChange={onSearchFieldChange}
          onSubmit={handleSearch}
        />
      </div>
    </section>
  );
};

import { AdvancedFilterState, Language } from '../types';
import { getLanguageFilterValues } from './languageFilters';

interface BuildSearchQueryOptions {
  searchInput: string;
  showAdvanced: boolean;
  advancedFilters: AdvancedFilterState;
  bookLanguages: Language[];
  defaultLanguage: string[];
}

export const buildSearchQuery = ({
  searchInput,
  showAdvanced,
  advancedFilters,
  bookLanguages,
  defaultLanguage,
}: BuildSearchQueryOptions): string => {
  const queryParts: string[] = [];

  const basic = searchInput.trim();
  if (basic) {
    queryParts.push(`query=${encodeURIComponent(basic)}`);
  }

  if (showAdvanced) {
    const { isbn, author, title, content, formats, lang } = advancedFilters;

    if (isbn) queryParts.push(`isbn=${encodeURIComponent(isbn)}`);
    if (author) queryParts.push(`author=${encodeURIComponent(author)}`);
    if (title) queryParts.push(`title=${encodeURIComponent(title)}`);

    const selectedLanguages = getLanguageFilterValues(lang, bookLanguages, defaultLanguage);
    selectedLanguages?.forEach(code => queryParts.push(`lang=${encodeURIComponent(code)}`));

    if (content) queryParts.push(`content=${encodeURIComponent(content)}`);
    formats.forEach(format => queryParts.push(`format=${encodeURIComponent(format)}`));
  }

  if (advancedFilters.sort) {
    queryParts.push(`sort=${encodeURIComponent(advancedFilters.sort)}`);
  }

  return queryParts.join('&');
};


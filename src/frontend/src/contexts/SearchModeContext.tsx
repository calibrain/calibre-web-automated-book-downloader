import { createContext, useContext, ReactNode } from 'react';
import { SearchMode } from '../types';

interface SearchModeContextValue {
  searchMode: SearchMode;
  isUniversalMode: boolean;
}

const SearchModeContext = createContext<SearchModeContextValue | null>(null);

export function useSearchMode(): SearchModeContextValue {
  const ctx = useContext(SearchModeContext);
  if (!ctx) {
    throw new Error('useSearchMode must be used within SearchModeProvider');
  }
  return ctx;
}

interface SearchModeProviderProps {
  searchMode: SearchMode;
  children: ReactNode;
}

export function SearchModeProvider({ searchMode, children }: SearchModeProviderProps) {
  return (
    <SearchModeContext.Provider value={{ searchMode, isUniversalMode: searchMode === 'universal' }}>
      {children}
    </SearchModeContext.Provider>
  );
}

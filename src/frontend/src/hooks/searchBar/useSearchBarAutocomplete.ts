import { useCallback, useMemo, useState } from 'react';

import { t } from '../../i18n';
import type { TextSearchField } from '../../types';

interface AutocompleteTextState {
  draftValue: string;
  fieldKey: string | null;
  syncedValue: string;
}

interface UseSearchBarAutocompleteOptions {
  field: TextSearchField | null;
  value: string | number | boolean;
  valueLabel?: string;
}

interface UseSearchBarAutocompleteReturn {
  autocompleteEndpoint: string | null;
  autocompleteMinQueryLength: number;
  textInputValue: string;
  autocompleteEmptyMessage: string;
  setAutocompleteDraftValue: (nextValue: string) => void;
  setAutocompleteSelection: (value: string, label: string) => void;
  resetAutocomplete: () => void;
}

const getAutocompleteDisplayValue = (
  value: string | number | boolean,
  valueLabel: string | undefined,
): string => {
  let nextValue = typeof value === 'string' ? value : String(value ?? '');
  if (valueLabel && typeof value === 'string' && value.trim() !== '') {
    nextValue = valueLabel;
  }
  return nextValue;
};

const getAutocompleteEmptyMessage = (fieldKey: string | null): string => {
  if (fieldKey === 'author') {
    return t('No authors found');
  }
  if (fieldKey === 'title') {
    return t('No titles found');
  }
  if (fieldKey === 'series') {
    return t('No series found');
  }
  return t('No suggestions found');
};

export const useSearchBarAutocomplete = ({
  field,
  value,
  valueLabel,
}: UseSearchBarAutocompleteOptions): UseSearchBarAutocompleteReturn => {
  const autocompleteEndpoint = field?.suggestions_endpoint ?? null;
  const autocompleteMinQueryLength = field?.suggestions_min_query_length ?? 2;
  const autocompleteFieldKey = autocompleteEndpoint ? (field?.key ?? null) : null;
  const externalAutocompleteValue = autocompleteEndpoint
    ? getAutocompleteDisplayValue(value, valueLabel)
    : '';

  const [autocompleteTextState, setAutocompleteTextState] = useState<AutocompleteTextState>(() => ({
    draftValue: externalAutocompleteValue,
    fieldKey: autocompleteFieldKey,
    syncedValue: externalAutocompleteValue,
  }));

  if (
    autocompleteTextState.fieldKey !== autocompleteFieldKey ||
    (autocompleteFieldKey !== null &&
      autocompleteTextState.syncedValue !== externalAutocompleteValue)
  ) {
    setAutocompleteTextState({
      draftValue: externalAutocompleteValue,
      fieldKey: autocompleteFieldKey,
      syncedValue: externalAutocompleteValue,
    });
  }

  const autocompleteEmptyMessage = useMemo(
    () => getAutocompleteEmptyMessage(autocompleteFieldKey),
    [autocompleteFieldKey],
  );

  const setAutocompleteDraftValue = useCallback((nextValue: string) => {
    setAutocompleteTextState((current) => ({
      ...current,
      draftValue: nextValue,
      syncedValue: nextValue,
    }));
  }, []);

  const setAutocompleteSelection = useCallback(
    (nextValue: string, label: string) => {
      setAutocompleteTextState({
        draftValue: label,
        fieldKey: autocompleteFieldKey,
        syncedValue: nextValue,
      });
    },
    [autocompleteFieldKey],
  );

  const resetAutocomplete = useCallback(() => {
    setAutocompleteTextState((current) => ({
      ...current,
      draftValue: '',
      syncedValue: '',
      fieldKey: autocompleteFieldKey,
    }));
  }, [autocompleteFieldKey]);

  return {
    autocompleteEndpoint,
    autocompleteMinQueryLength,
    textInputValue: autocompleteTextState.draftValue,
    autocompleteEmptyMessage,
    setAutocompleteDraftValue,
    setAutocompleteSelection,
    resetAutocomplete,
  };
};

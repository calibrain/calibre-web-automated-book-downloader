import type { CSSProperties } from 'react';
import { useMemo } from 'react';

import { t } from '../../../i18n';
import type {
  MultiSelectFieldConfig,
  TableFieldConfig,
  TableFieldColumn,
} from '../../../types/settings';
import { DropdownList } from '../../DropdownList';
import { MultiSelectField } from './MultiSelectField';

interface TableFieldProps {
  field: TableFieldConfig;
  value: Record<string, unknown>[];
  onChange: (value: Record<string, unknown>[]) => void;
  disabled?: boolean;
}

function toPrimitiveString(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return '';
}

function toOptionalPrimitiveString(value: unknown): string | undefined {
  if (value === undefined || value === null || value === '') {
    return undefined;
  }

  const normalizedValue = toPrimitiveString(value);
  return normalizedValue || undefined;
}

function defaultCellValue(column: TableFieldColumn): unknown {
  if (column.defaultValue !== undefined) {
    return column.defaultValue;
  }
  if (column.type === 'multiselect') {
    return [];
  }
  if (column.type === 'checkbox') {
    return false;
  }
  return '';
}

function normalizeMultiValue(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((entry) => toPrimitiveString(entry).trim())
      .filter((entry) => entry.length > 0);
  }
  if (typeof value === 'string') {
    const normalized = value.trim();
    return normalized ? [normalized] : [];
  }
  return [];
}

function normalizeRows(
  rows: Record<string, unknown>[],
  columns: TableFieldColumn[],
): Record<string, unknown>[] {
  return rows.map((row) => {
    const normalized: Record<string, unknown> = Object.assign({}, row);
    for (const col of columns) {
      if (!(col.key in normalized)) {
        normalized[col.key] = defaultCellValue(col);
      }
    }
    return normalized;
  });
}

function normalizeRowDependencies(
  row: Record<string, unknown>,
  columns: TableFieldColumn[],
): Record<string, unknown> {
  const normalizedRow: Record<string, unknown> = Object.assign({}, row);

  columns.forEach((col) => {
    if (col.type === 'multiselect') {
      const filteredOptions = getFilteredSelectOptions(col, normalizedRow);
      const validValues = new Set(filteredOptions.map((opt) => opt.value));
      const currentValues = normalizeMultiValue(normalizedRow[col.key]);

      normalizedRow[col.key] = currentValues.filter((entry) => validValues.has(entry));
      return;
    }

    if (col.type !== 'select') {
      return;
    }

    const filteredOptions = getFilteredSelectOptions(col, normalizedRow);
    const currentValue = toPrimitiveString(normalizedRow[col.key]);
    const currentValueIsValid = filteredOptions.some((opt) => opt.value === currentValue);
    const nonEmptyOptions = filteredOptions.filter((opt) => opt.value !== '');

    if (nonEmptyOptions.length === 1) {
      normalizedRow[col.key] = nonEmptyOptions[0].value;
      return;
    }

    if (currentValue && !currentValueIsValid) {
      normalizedRow[col.key] = '';
    }
  });

  return normalizedRow;
}

function normalizeTableRows(
  rows: Record<string, unknown>[],
  columns: TableFieldColumn[],
): Record<string, unknown>[] {
  return normalizeRows(rows, columns).map((row) => normalizeRowDependencies(row, columns));
}

function getFilteredSelectOptions(
  column: TableFieldColumn,
  row: Record<string, unknown>,
): Array<{ value: string; label: string; description?: string; childOf?: string }> {
  const options = (column.options ?? []).map((opt) => ({
    value: opt.value,
    label: opt.label ?? opt.value,
    description: opt.description,
    childOf: opt.childOf,
  }));

  const filterByField = column.filterByField;
  if (!filterByField) {
    return options.filter((opt) => !opt.childOf);
  }

  const rawFilterValue = row[filterByField];
  const filterValue = toOptionalPrimitiveString(rawFilterValue);

  if (!filterValue) {
    return options.filter((opt) => !opt.childOf);
  }

  return options.filter((opt) => !opt.childOf || opt.childOf === filterValue);
}

function serializeRowKeyValue(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map((entry) => serializeRowKeyValue(entry)).join('|');
  }

  try {
    return JSON.stringify(value) ?? '';
  } catch {
    return '[unserializable]';
  }
}

export const TableField = ({ field, value, onChange, disabled }: TableFieldProps) => {
  const isDisabled = disabled ?? false;

  const columns = useMemo(() => field.columns ?? [], [field.columns]);
  const rows = useMemo(() => normalizeTableRows(value ?? [], columns), [value, columns]);
  const rowEntries = useMemo(() => {
    const rowOccurrences = new Map<string, number>();

    return rows.map((row) => {
      const baseKey = columns
        .map((col) => `${col.key}:${serializeRowKeyValue(row[col.key])}`)
        .join('||');
      const occurrence = rowOccurrences.get(baseKey) ?? 0;

      rowOccurrences.set(baseKey, occurrence + 1);

      return {
        row,
        key: `${baseKey}::${occurrence}`,
      };
    });
  }, [rows, columns]);

  // Use minmax(0, ...) so the grid can shrink inside the settings modal.
  // Use fixed width for delete button column to ensure header/data alignment.
  const gridTemplate = 'sm:grid-cols-(--table-cols)';

  const tableCols = useMemo(() => {
    if (columns.length === 0) {
      return 'minmax(0,1fr) 2rem';
    }

    const colDefs = columns.map((_, idx) => (idx === 0 ? 'minmax(0,180px)' : 'minmax(0,1fr)'));
    return `${colDefs.join(' ')} 2rem`;
  }, [columns]);
  const tableStyle: CSSProperties & { '--table-cols': string } = { '--table-cols': tableCols };

  const commitRows = (nextRows: Record<string, unknown>[]) => {
    onChange(normalizeTableRows(nextRows, columns));
  };

  const updateCell = (rowIndex: number, key: string, cellValue: unknown) => {
    const next = rows.map((row, idx) => (idx === rowIndex ? { ...row, [key]: cellValue } : row));
    commitRows(next);
  };

  const addRow = () => {
    const newRow: Record<string, unknown> = {};
    columns.forEach((col) => {
      newRow[col.key] = defaultCellValue(col);
    });
    commitRows([...rows, newRow]);
  };

  const removeRow = (rowIndex: number) => {
    const next = rows.filter((_, idx) => idx !== rowIndex);
    commitRows(next);
  };

  if (rows.length === 0) {
    return (
      <div className="space-y-3">
        {field.emptyMessage && <p className="text-sm opacity-70">{field.emptyMessage}</p>}
        <button
          type="button"
          onClick={addRow}
          disabled={isDisabled}
          className="hover-action rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60"
        >
          {field.addLabel || t('Add')}
        </button>
      </div>
    );
  }

  return (
    <div className="min-w-0 space-y-3" style={tableStyle}>
      <div
        className={`hidden sm:grid ${gridTemplate} min-w-0 items-start gap-3 text-xs font-medium opacity-70`}
      >
        {columns.map((col) => (
          <div key={col.key} className="min-w-0 truncate">
            {col.label}
          </div>
        ))}
        <div />
      </div>

      <div className="min-w-0 space-y-3">
        {rowEntries.map(({ row, key: rowKey }, rowIndex) => (
          <div
            key={rowKey}
            className={`grid grid-cols-1 ${gridTemplate} min-w-0 items-start gap-3`}
            style={{ overflow: 'visible' }}
          >
            {columns.map((col) => {
              const cellValue = row[col.key];

              const mobileLabel = (
                <div className="text-xs font-medium opacity-70 sm:hidden">{col.label}</div>
              );

              if (col.type === 'checkbox') {
                return (
                  <div key={col.key} className="flex min-w-0 flex-col gap-1">
                    {mobileLabel}
                    <div className="pt-2">
                      <input
                        type="checkbox"
                        checked={Boolean(cellValue)}
                        onChange={(e) => updateCell(rowIndex, col.key, e.target.checked)}
                        disabled={isDisabled}
                        aria-label={t('{column} row {row}', {
                          column: col.label || col.key,
                          row: rowIndex + 1,
                        })}
                        className="h-4 w-4 rounded border-gray-300 text-sky-600 focus:ring-sky-500 disabled:cursor-not-allowed disabled:opacity-60"
                      />
                    </div>
                  </div>
                );
              }

              if (col.type === 'select') {
                const options = getFilteredSelectOptions(col, row).map((opt) => ({
                  value: opt.value,
                  label: opt.label,
                  description: opt.description,
                }));

                return (
                  <div key={col.key} className="flex min-w-0 flex-col gap-1">
                    {mobileLabel}
                    {isDisabled ? (
                      <div className="w-full cursor-not-allowed rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-2 text-sm opacity-60 shadow-sm">
                        {options.find((o) => o.value === toPrimitiveString(cellValue))?.label ||
                          t('Select...')}
                      </div>
                    ) : (
                      <DropdownList
                        options={options}
                        value={toPrimitiveString(cellValue)}
                        onChange={(val) =>
                          updateCell(rowIndex, col.key, Array.isArray(val) ? val[0] : val)
                        }
                        placeholder={col.placeholder || t('Select...')}
                        widthClassName="w-full"
                      />
                    )}
                  </div>
                );
              }

              if (col.type === 'multiselect') {
                const options = getFilteredSelectOptions(col, row).map((opt) => ({
                  value: opt.value,
                  label: opt.label,
                  description: opt.description,
                  childOf: opt.childOf,
                }));
                const selectedValues = normalizeMultiValue(cellValue).filter((entry) =>
                  options.some((option) => option.value === entry),
                );
                const multiSelectField: MultiSelectFieldConfig = {
                  type: 'MultiSelectField',
                  key: `${field.key}_${rowIndex}_${col.key}`,
                  label: col.label,
                  value: selectedValues,
                  options,
                  variant: 'dropdown',
                  placeholder: col.placeholder || t('Select...'),
                };

                return (
                  <div key={col.key} className="flex min-w-0 flex-col gap-1">
                    {mobileLabel}
                    <MultiSelectField
                      field={multiSelectField}
                      value={selectedValues}
                      onChange={(nextValues) => {
                        const normalizedValues = (nextValues ?? [])
                          .map((entry) => entry.trim())
                          .filter((entry) => entry.length > 0);
                        updateCell(rowIndex, col.key, normalizedValues);
                      }}
                      disabled={isDisabled}
                    />
                  </div>
                );
              }

              // text/password/path
              return (
                <div key={col.key} className="flex min-w-0 flex-col gap-1">
                  {mobileLabel}
                  <input
                    type={col.type === 'password' ? 'password' : 'text'}
                    value={toPrimitiveString(cellValue)}
                    onChange={(e) => updateCell(rowIndex, col.key, e.target.value)}
                    placeholder={col.placeholder}
                    aria-label={t('{column} row {row}', {
                      column: col.label || col.key,
                      row: rowIndex + 1,
                    })}
                    disabled={isDisabled}
                    className="w-full rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-2 text-sm transition-colors focus:border-sky-500 focus:ring-2 focus:ring-sky-500/50 focus:outline-hidden disabled:cursor-not-allowed disabled:opacity-60"
                  />
                </div>
              );
            })}

            <div className="flex items-start pt-1.5">
              <button
                type="button"
                onClick={() => removeRow(rowIndex)}
                disabled={isDisabled}
                className="hover-action rounded-full p-1.5 disabled:cursor-not-allowed disabled:opacity-60"
                aria-label={t('Remove row')}
              >
                <svg
                  className="h-4 w-4"
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={1.5}
                  stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <div className="col-span-full border-t border-(--border-muted) opacity-60" />
          </div>
        ))}
      </div>

      <button
        type="button"
        onClick={addRow}
        disabled={isDisabled}
        className="hover-action rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60"
      >
        {field.addLabel || t('Add')}
      </button>
    </div>
  );
};

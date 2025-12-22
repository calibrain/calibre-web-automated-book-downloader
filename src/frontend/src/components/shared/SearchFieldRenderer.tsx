import { KeyboardEvent } from 'react';
import { MetadataSearchField } from '../../types';
import { DropdownList } from '../DropdownList';

interface SearchFieldRendererProps {
  field: MetadataSearchField;
  value: string | number | boolean;
  onChange: (value: string | number | boolean) => void;
  onSubmit?: () => void;
}

const baseInputClass =
  'w-full px-3 py-2 rounded-md border border-[var(--border-muted)] ' +
  'bg-[var(--bg-soft)] text-sm ' +
  'focus:outline-none focus:ring-2 focus:ring-emerald-500/50 focus:border-emerald-500 ' +
  'transition-colors';

export const SearchFieldRenderer = ({ field, value, onChange, onSubmit }: SearchFieldRendererProps) => {
  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && onSubmit) {
      e.preventDefault();
      onSubmit();
    }
  };
  switch (field.type) {
    case 'TextSearchField':
      return (
        <input
          type="text"
          value={(value as string) ?? ''}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={field.placeholder}
          autoComplete="off"
          enterKeyHint="search"
          className={baseInputClass}
          style={{
            background: 'var(--bg-soft)',
            color: 'var(--text)',
            borderColor: 'var(--border-muted)',
          }}
        />
      );

    case 'NumberSearchField': {
      const handleNumberChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const raw = e.target.value;
        if (!raw) {
          onChange('');
          return;
        }
        const num = parseInt(raw, 10);
        if (!isNaN(num)) {
          onChange(num);
        }
      };
      return (
        <input
          type="number"
          value={value === '' ? '' : (value as number)}
          onChange={handleNumberChange}
          onKeyDown={handleKeyDown}
          placeholder={field.placeholder}
          enterKeyHint="search"
          min={field.min}
          max={field.max}
          step={field.step}
          className={baseInputClass}
          style={{
            background: 'var(--bg-soft)',
            color: 'var(--text)',
            borderColor: 'var(--border-muted)',
          }}
        />
      );
    }

    case 'SelectSearchField':
      return (
        <DropdownList
          options={field.options}
          value={(value as string) ?? ''}
          onChange={(v) => onChange(Array.isArray(v) ? v[0] ?? '' : v)}
          placeholder="All"
        />
      );

    case 'CheckboxSearchField':
      return (
        <label className="flex items-center gap-2 cursor-pointer py-2">
          <input
            type="checkbox"
            checked={!!value}
            onChange={(e) => onChange(e.target.checked)}
            className="w-4 h-4 rounded border-[var(--border-muted)] text-emerald-500 focus:ring-emerald-500/50"
          />
          <span className="text-sm">{field.label}</span>
        </label>
      );

    default:
      return null;
  }
};

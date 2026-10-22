import { t } from '../../i18n';
import type { DisplayField } from '../../types';

interface DisplayFieldIconProps {
  icon?: string;
  className?: string;
}

export function DisplayFieldIcon({ icon, className = 'w-3 h-3' }: DisplayFieldIconProps) {
  switch (icon) {
    case undefined:
      return null;
    case 'star':
      return (
        <svg className={className} fill="currentColor" viewBox="0 0 20 20">
          <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
        </svg>
      );
    case 'book':
      return (
        <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"
          />
        </svg>
      );
    case 'users':
      return (
        <svg
          className={className}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M15 19.128a9.38 9.38 0 0 0 2.625.372 9.337 9.337 0 0 0 4.121-.952 4.125 4.125 0 0 0-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 0 1 8.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0 1 11.964-3.07M12 6.375a3.375 3.375 0 1 1-6.75 0 3.375 3.375 0 0 1 6.75 0Zm8.25 2.25a2.625 2.625 0 1 1-5.25 0 2.625 2.625 0 0 1 5.25 0Z"
          />
        </svg>
      );
    case 'microphone':
      return (
        <svg
          className={className}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 18.75a6 6 0 0 0 6-6v-1.5m-6 7.5a6 6 0 0 1-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 0 1-3-3V4.5a3 3 0 1 1 6 0v8.25a3 3 0 0 1-3 3Z"
          />
        </svg>
      );
    case 'clock':
      return (
        <svg
          className={className}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          strokeWidth={1.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
          />
        </svg>
      );
    case 'editions':
      return (
        <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"
          />
        </svg>
      );
    default:
      return null;
  }
}

interface DisplayFieldBadgeProps {
  field: DisplayField;
  className?: string;
}

const getKeyedDisplayFields = (fields: DisplayField[]) => {
  const signatureCounts = new Map<string, number>();

  return fields.map((field) => {
    const signature = [field.icon ?? 'none', field.label, field.value].join('|');
    const nextCount = (signatureCounts.get(signature) ?? 0) + 1;
    signatureCounts.set(signature, nextCount);

    return {
      field,
      key: nextCount === 1 ? signature : `${signature}|${nextCount}`,
    };
  });
};

export function DisplayFieldBadge({ field, className = '' }: DisplayFieldBadgeProps) {
  return (
    <span
      className={`flex items-center gap-0.5 text-xs text-gray-700 dark:text-gray-200 ${className}`}
      title={t(field.label)}
    >
      <DisplayFieldIcon icon={field.icon} />
      <span>{field.value}</span>
    </span>
  );
}

interface DisplayFieldBadgesProps {
  fields?: DisplayField[];
  className?: string;
}

export function DisplayFieldBadges({ fields, className = '' }: DisplayFieldBadgesProps) {
  if (!fields || fields.length === 0) return null;

  const keyedFields = getKeyedDisplayFields(fields);

  return (
    <div className={`flex flex-wrap gap-1.5 ${className}`}>
      {keyedFields.map(({ field, key }) => (
        <DisplayFieldBadge key={key} field={field} />
      ))}
    </div>
  );
}

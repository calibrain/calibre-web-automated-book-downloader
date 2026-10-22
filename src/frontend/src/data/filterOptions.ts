import { t } from '../i18n';

// Direct download mode sort options
export const SORT_OPTIONS = [
  { value: 'downloads', label: 'Most downloads' },
  { value: 'relevance', label: 'Most relevant' },
  { value: 'newest', label: 'Newest (publication year)' },
  { value: 'oldest', label: 'Oldest (publication year)' },
  { value: 'largest', label: 'Largest (filesize)' },
  { value: 'smallest', label: 'Smallest (filesize)' },
  { value: 'newest_added', label: 'Newest (open sourced)' },
  { value: 'oldest_added', label: 'Oldest (open sourced)' },
];

// Note: Metadata mode sort options are now dynamic per provider
// They come from the /api/config endpoint as metadata_sort_options

// Direct download mode content type options
export const CONTENT_OPTIONS = [
  { value: '', label: t('All') },
  { value: 'book_nonfiction', label: t('Book (non-fiction)') },
  { value: 'book_fiction', label: t('Book (fiction)') },
  { value: 'book_unknown', label: t('Book (unknown)') },
  { value: 'magazine', label: t('Magazine') },
  { value: 'book_comic', label: t('Comic Book') },
  { value: 'standards_document', label: t('Standards document') },
  { value: 'other', label: t('Other') },
  { value: 'musical_score', label: t('Musical score') },
];

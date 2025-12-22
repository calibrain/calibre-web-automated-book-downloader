// Direct download mode sort options (Anna's Archive)
export const SORT_OPTIONS = [
  { value: '', label: 'Most relevant' },
  { value: 'newest', label: 'Newest (publication year)' },
  { value: 'oldest', label: 'Oldest (publication year)' },
  { value: 'largest', label: 'Largest (filesize)' },
  { value: 'smallest', label: 'Smallest (filesize)' },
  { value: 'newest_added', label: 'Newest (open sourced)' },
  { value: 'oldest_added', label: 'Oldest (open sourced)' },
];

// Note: Metadata mode sort options are now dynamic per provider
// They come from the /api/config endpoint as metadata_sort_options

// Direct download mode content type options (Anna's Archive)
export const CONTENT_OPTIONS = [
  { value: '', label: 'All' },
  { value: 'book_nonfiction', label: 'Book (non-fiction)' },
  { value: 'book_fiction', label: 'Book (fiction)' },
  { value: 'book_unknown', label: 'Book (unknown)' },
  { value: 'magazine', label: 'Magazine' },
  { value: 'book_comic', label: 'Comic Book' },
  { value: 'standards_document', label: 'Standards document' },
  { value: 'other', label: 'Other' },
  { value: 'musical_score', label: 'Musical score' },
  { value: 'audiobook', label: 'Audiobook' },
];


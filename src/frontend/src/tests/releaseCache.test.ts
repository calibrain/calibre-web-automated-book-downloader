import { describe, it, expect } from 'vitest';

import type { ReleasesResponse } from '../types/index';
import {
  getCachedReleases,
  invalidateCachedReleases,
  setCachedReleases,
} from '../utils/releaseCache';

// Open Library reports every translation of a book under one work ID, so the
// German and the English edition of the same book share `provider_id`.
const germanEdition = {
  provider: 'openlibrary',
  provider_id: 'OL15358691W',
  title: 'Der Weg der Könige',
  language: 'ger',
};

const englishEdition = {
  provider: 'openlibrary',
  provider_id: 'OL15358691W',
  title: 'The Way of Kings',
  language: 'eng',
};

const response = (title: string): ReleasesResponse => ({
  releases: [],
  book: { provider: 'openlibrary', provider_id: 'OL15358691W', title },
  sources_searched: ['prowlarr'],
});

describe('releaseCache', () => {
  it('keeps editions of the same work apart', () => {
    setCachedReleases(germanEdition, 'prowlarr', 'ebook', response('Der Weg der Könige'));

    expect(getCachedReleases(englishEdition, 'prowlarr', 'ebook')).toBeNull();
    expect(getCachedReleases(germanEdition, 'prowlarr', 'ebook')).toEqual(
      response('Der Weg der Könige'),
    );
  });

  it('invalidates only the edition it was given', () => {
    setCachedReleases(germanEdition, 'prowlarr', 'audiobook', response('Der Weg der Könige'));
    setCachedReleases(englishEdition, 'prowlarr', 'audiobook', response('The Way of Kings'));

    invalidateCachedReleases(germanEdition, 'prowlarr', 'audiobook');

    expect(getCachedReleases(germanEdition, 'prowlarr', 'audiobook')).toBeNull();
    expect(getCachedReleases(englishEdition, 'prowlarr', 'audiobook')).toEqual(
      response('The Way of Kings'),
    );
  });
});

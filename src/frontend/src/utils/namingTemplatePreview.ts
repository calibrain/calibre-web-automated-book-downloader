export type NamingTemplateMode = 'filename' | 'path';
export type NamingTemplateContent = 'book' | 'audiobook';

export interface NamingTemplateToken {
  token: string;
  label: string;
  description: string;
  value: string;
  group: 'Core' | 'Universal' | 'Files';
  audiobookOnly?: boolean;
}

interface RenderOptions {
  allowPathSeparators: boolean;
}

interface RenderResult {
  value: string;
  unknownTokens: string[];
}

export const NAMING_TEMPLATE_TOKENS: NamingTemplateToken[] = [
  {
    token: 'Author',
    label: 'Author',
    description: 'Primary author',
    value: 'Arthur Conan Doyle',
    group: 'Core',
  },
  {
    token: 'FirstAuthor',
    label: 'First author',
    description: 'First author only, when metadata lists several',
    value: 'Arthur Conan Doyle',
    group: 'Core',
  },
  {
    token: 'Title',
    label: 'Full title',
    description: 'Title as provided by metadata',
    value: 'The Hound of the Baskervilles: Another Adventure of Sherlock Holmes',
    group: 'Core',
  },
  {
    token: 'PrimaryTitle',
    label: 'Primary title',
    description: 'Title without the subtitle suffix',
    value: 'The Hound of the Baskervilles',
    group: 'Universal',
  },
  {
    token: 'Year',
    label: 'Year',
    description: 'Publication year',
    value: '1902',
    group: 'Core',
  },
  {
    token: 'Language',
    label: 'Language',
    description: 'Release language code, so translations do not share a folder',
    value: 'en',
    group: 'Core',
  },
  {
    token: 'User',
    label: 'User',
    description: 'Requesting user',
    value: 'alex',
    group: 'Core',
  },
  {
    token: 'Series',
    label: 'Series',
    description: 'Series name',
    value: 'Sherlock Holmes',
    group: 'Universal',
  },
  {
    token: 'SeriesPosition',
    label: 'Series position',
    description: 'Book position in the series',
    value: '5',
    group: 'Universal',
  },
  {
    token: 'Subtitle',
    label: 'Subtitle',
    description: 'Subtitle from metadata',
    value: 'Another Adventure of Sherlock Holmes',
    group: 'Universal',
  },
  {
    token: 'OriginalName',
    label: 'Original name',
    description: 'Source filename without extension',
    value: 'The Hound of the Baskervilles - Chapter 01',
    group: 'Files',
  },
  {
    token: 'PartNumber',
    label: 'Part number',
    description: 'Sequential part number for multi-file audiobooks',
    value: '01',
    group: 'Files',
    audiobookOnly: true,
  },
];

const KNOWN_TOKENS = [
  'seriesposition',
  'primarytitle',
  'originalname',
  'firstauthor',
  'partnumber',
  'language',
  'subtitle',
  'author',
  'series',
  'title',
  'year',
  'user',
];

// Mirrors AUTHOR_LIST_SEPARATOR in shelfmark/core/naming.py: authors arrive
// pre-joined with ',' or ';' and {FirstAuthor} keeps only the first entry.
const firstAuthor = (value: string): string => value.split(/\s*[,;]\s*/)[0]?.trim() ?? '';

const BRACE_PATTERN = /\{([^}]+)\}/g;
const INVALID_CHARS_PATTERN = /[\\/:*?"<>|]/g;

export const SAMPLE_NAMING_METADATA = NAMING_TEMPLATE_TOKENS.reduce<Record<string, string>>(
  (metadata, token) => {
    metadata[token.token] = token.value;
    return metadata;
  },
  {},
);

const sanitizeFilename = (value: string): string => {
  return value
    .replace(INVALID_CHARS_PATTERN, '_')
    .replace(/^[\s.]+|[\s.]+$/g, '')
    .replace(/_+/g, '_')
    .slice(0, 245);
};

const normalizeMetadata = (metadata: Record<string, string>): Record<string, string> => {
  return Object.fromEntries(
    Object.entries(metadata).map(([key, value]) => [key.toLowerCase(), value]),
  );
};

const findPlaceholder = (content: string): { name: string | null; index: number } => {
  const lowerContent = content.toLowerCase();
  for (const tokenName of KNOWN_TOKENS) {
    const index = lowerContent.indexOf(tokenName);
    if (index !== -1) {
      return { name: tokenName, index };
    }
  }
  return { name: null, index: -1 };
};

export const renderNamingTemplate = (
  template: string,
  metadata: Record<string, string> = SAMPLE_NAMING_METADATA,
  options: RenderOptions,
): RenderResult => {
  if (!template) {
    return { value: '', unknownTokens: [] };
  }

  const normalized = normalizeMetadata(metadata);
  const unknownTokens: string[] = [];

  const placeholderValue = (placeholderName: string): string => {
    if (placeholderName === 'firstauthor' && !normalized['firstauthor']) {
      return firstAuthor(normalized['author'] ?? '');
    }
    return (normalized[placeholderName] ?? '').trim();
  };

  const renderBlock = (content: string): string | null => {
    const { name, index } = findPlaceholder(content);
    if (!name) {
      const unknown = content.trim();
      if (unknown && !/\s/.test(unknown) && !unknownTokens.includes(unknown)) {
        unknownTokens.push(unknown);
      }
      return null;
    }

    const prefix = content.slice(0, index);
    const suffix = content.slice(index + name.length);
    const rawValue = placeholderValue(name);
    if (!rawValue) {
      return '';
    }

    const value = sanitizeFilename(
      options.allowPathSeparators ? rawValue : rawValue.replace(/\//g, '_'),
    );
    return `${prefix}${value}${suffix}`;
  };

  const matches = Array.from(template.matchAll(BRACE_PATTERN));
  let result = '';

  if (matches.length === 0) {
    result = template;
  } else {
    let cursor = 0;
    matches.forEach((match, index) => {
      result += template.slice(cursor, match.index);
      const content = match[1] ?? '';
      const rendered = renderBlock(content);

      if (rendered !== null) {
        result += rendered;
      } else {
        const nextMatch = matches[index + 1];
        const conditionalLiteral =
          nextMatch !== undefined && match.index + match[0].length === nextMatch.index;
        const nextContent = nextMatch?.[1] ?? '';
        const nextPlaceholder = findPlaceholder(nextContent).name;
        const includeLiteral =
          conditionalLiteral && nextPlaceholder
            ? Boolean(placeholderValue(nextPlaceholder))
            : false;

        if (includeLiteral) {
          result += content;
        } else if (!conditionalLiteral && /\s/.test(content)) {
          result += match[0];
        }
      }

      cursor = match.index + match[0].length;
    });
    result += template.slice(cursor);
  }

  result = result.replace(/\/+/g, '/');
  result = result.replace(/^\/+|\/+$/g, '');
  result = result.replace(/^[\s\-_.]+/g, '');
  result = result.replace(/[\s\-_.]+$/g, '');
  result = result.replace(/(\s*-\s*){2,}/g, ' - ');
  result = result.replace(/\(\s*\)/g, '');
  result = result.replace(/\[\s*\]/g, '');
  result = result.replace(/[\s\-_.]+$/g, '');

  return { value: result, unknownTokens };
};

export const buildNamingTemplatePreview = (
  template: string,
  mode: NamingTemplateMode,
  content: NamingTemplateContent,
): RenderResult => {
  const rendered = renderNamingTemplate(template, SAMPLE_NAMING_METADATA, {
    allowPathSeparators: mode === 'path',
  });
  const fallback = SAMPLE_NAMING_METADATA.PrimaryTitle;
  const extension = content === 'audiobook' ? 'mp3' : 'epub';
  const baseValue = rendered.value || fallback;

  return {
    value: `${baseValue}.${extension}`,
    unknownTokens: rendered.unknownTokens,
  };
};

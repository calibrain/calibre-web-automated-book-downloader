export interface ReviewFile {
  path: string;
  size: number | null;
}

export interface FormatGroup {
  /** Lowercase extension without the dot, e.g. "epub". */
  format: string;
  files: ReviewFile[];
  /** Sum of known file sizes in bytes; null when no file reports a size. */
  totalBytes: number | null;
}

/** The lowercase extension of a release-relative path ("" when it has none). */
export function fileFormat(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? '';
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : '';
}

/** Group the release's book files by format, largest group first. */
export function groupFilesByFormat(
  files: ReviewFile[],
  bookFiles: ReadonlySet<string>,
): FormatGroup[] {
  const groups = new Map<string, FormatGroup>();
  for (const file of files) {
    if (!bookFiles.has(file.path)) continue;
    const format = fileFormat(file.path);
    const group = groups.get(format) ?? { format, files: [], totalBytes: null };
    group.files.push(file);
    if (file.size !== null) group.totalBytes = (group.totalBytes ?? 0) + file.size;
    groups.set(format, group);
  }
  return [...groups.values()].toSorted(
    (a, b) => b.files.length - a.files.length || a.format.localeCompare(b.format),
  );
}

/** Every file belonging to a format that is not selected. */
export function ignoredForFormats(groups: FormatGroup[], selected: ReadonlySet<string>): string[] {
  return groups.filter((g) => !selected.has(g.format)).flatMap((g) => g.files.map((f) => f.path));
}

/** "1.5 MB", "820 KB"; empty string for an unknown size. */
export function formatBytes(bytes: number | null): string {
  if (bytes === null) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || value >= 100 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[unit]}`;
}

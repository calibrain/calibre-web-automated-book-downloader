/**
 * Get a nested value from an object using dot-notation path.
 * e.g., getNestedValue(obj, "extra.language") returns obj.extra.language
 */
export function getNestedValue(obj: Record<string, unknown>, path: string): unknown {
  return path.split('.').reduce((current, key) => {
    if (current && typeof current === 'object') {
      return (current as Record<string, unknown>)[key];
    }
    return undefined;
  }, obj as unknown);
}

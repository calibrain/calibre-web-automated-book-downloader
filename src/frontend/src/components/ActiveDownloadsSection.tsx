import { Book } from '../types';

interface ActiveDownloadsSectionProps {
  downloads: Book[];
  visible: boolean;
  onRefresh: () => void;
  onCancel: (id: string) => void;
}

export const ActiveDownloadsSection = ({
  downloads,
  visible,
  onRefresh,
  onCancel,
}: ActiveDownloadsSectionProps) => {
  if (!visible) return null;

  return (
    <section id="active-downloads-top" className="mb-6">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-lg font-semibold">Active Downloads</h2>
        <button
          onClick={onRefresh}
          className="px-3 py-1 rounded border text-sm"
          style={{ borderColor: 'var(--border-muted)' }}
        >
          Refresh
        </button>
      </div>
      <div id="active-downloads-list" className="space-y-2">
        {downloads.map(book => (
          <div
            key={book.id}
            className="p-3 rounded border"
            style={{ borderColor: 'var(--border-muted)', background: 'var(--bg-soft)' }}
          >
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm truncate">
                <strong>{book.title || '-'}</strong>
              </div>
              <div className="shrink-0">
                <button
                  className="px-2 py-0.5 rounded border text-xs"
                  style={{ borderColor: 'var(--border-muted)' }}
                  onClick={() => onCancel(book.id)}
                >
                  Cancel
                </button>
              </div>
            </div>
            {typeof book.progress === 'number' && (
              <div className="h-1.5 bg-black/10 rounded overflow-hidden">
                <div
                  className="h-1.5 bg-blue-600"
                  style={{ width: `${Math.round(book.progress)}%` }}
                />
              </div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
};

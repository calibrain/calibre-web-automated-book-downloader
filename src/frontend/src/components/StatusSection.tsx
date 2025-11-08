import { StatusData } from '../types';

interface StatusSectionProps {
  status: StatusData;
  visible: boolean;
  activeCount: number;
  onRefresh: () => void;
  onClearCompleted: () => void;
  onCancel: (id: string) => void;
}

export const StatusSection = ({
  status,
  visible,
  activeCount,
  onRefresh,
  onClearCompleted,
  onCancel,
}: StatusSectionProps) => {
  if (!visible) return null;

  const renderSection = (name: string, items: Record<string, any>) => {
    if (!items || Object.keys(items).length === 0) return null;

    return (
      <div key={name}>
        <h4 className="font-semibold mb-2">
          {name.charAt(0).toUpperCase() + name.slice(1)}
        </h4>
        <ul className="space-y-2">
          {Object.values(items).map((book: any) => {
            const maybeLinkedTitle = book.download_path ? (
              <a
                href={`/request/api/localdownload?id=${encodeURIComponent(book.id)}`}
                className="text-blue-600 hover:underline"
              >
                {book.title || '-'}
              </a>
            ) : (
              book.title || '-'
            );

            const actions =
              name === 'queued' || name === 'downloading' ? (
                <button
                  className="px-2 py-1 rounded border text-xs"
                  style={{ borderColor: 'var(--border-muted)' }}
                  onClick={() => onCancel(book.id)}
                >
                  Cancel
                </button>
              ) : null;

            const progress =
              name === 'downloading' && typeof book.progress === 'number' ? (
                <div className="h-2 bg-black/10 rounded overflow-hidden">
                  <div
                    className="h-2 bg-blue-600"
                    style={{ width: `${Math.round(book.progress)}%` }}
                  />
                </div>
              ) : null;

            return (
              <li
                key={book.id}
                className="p-3 rounded border flex flex-col gap-2"
                style={{ borderColor: 'var(--border-muted)', background: 'var(--bg-soft)' }}
              >
                <div className="text-sm">
                  <span className="opacity-70">{name}</span> • <strong>{maybeLinkedTitle}</strong>
                </div>
                {progress}
                {actions && <div className="flex items-center gap-2">{actions}</div>}
              </li>
            );
          })}
        </ul>
      </div>
    );
  };

  const sections = ['queued', 'downloading', 'completed', 'available', 'done', 'error'];
  const renderedSections = sections
    .map(name => renderSection(name, (status as any)[name] || {}))
    .filter(Boolean);

  return (
    <section id="status-section">
      <div className="flex items-center flex-wrap mb-3">
        <h2 className="text-xl font-semibold mr-4 sm:mr-6">Download Queue & Status</h2>
        <div className="flex items-center gap-3 ml-4 sm:ml-auto">
          <button
            onClick={onRefresh}
            className="px-3 py-1 rounded border text-sm"
            style={{ borderColor: 'var(--border-muted)' }}
          >
            Refresh
          </button>
          <button
            onClick={onClearCompleted}
            className="px-3 py-1 rounded border text-sm"
            style={{ borderColor: 'var(--border-muted)' }}
          >
            Clear Completed
          </button>
          <span className="text-sm opacity-80">Active: {activeCount}</span>
        </div>
      </div>
      <div id="status-list" className="space-y-2">
        {renderedSections.length > 0 ? (
          renderedSections
        ) : (
          <div className="text-sm opacity-80">No items.</div>
        )}
      </div>
    </section>
  );
};

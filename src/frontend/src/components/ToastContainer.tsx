import { useEffect, useState } from 'react';
import { Toast } from '../types';

interface ToastContainerProps {
  toasts: Toast[];
}

export const ToastContainer = ({ toasts }: ToastContainerProps) => {
  const [visibleToasts, setVisibleToasts] = useState<Set<string>>(new Set());

  useEffect(() => {
    toasts.forEach(toast => {
      if (!visibleToasts.has(toast.id)) {
        setTimeout(() => {
          setVisibleToasts(prev => new Set([...prev, toast.id]));
        }, 10);
      }
    });
  }, [toasts]);

  return (
    <div id="toast-container" className="fixed top-4 right-4 z-50 space-y-2">
      {toasts.map(toast => (
        <div
          key={toast.id}
          className={`toast-notification px-4 py-3 rounded-md shadow-lg text-sm font-medium transition-all duration-300 ${
            toast.type === 'success' ? 'bg-green-600 text-white' : 'bg-blue-600 text-white'
          } ${visibleToasts.has(toast.id) ? 'toast-visible' : ''}`}
        >
          {toast.message}
        </div>
      ))}
    </div>
  );
};

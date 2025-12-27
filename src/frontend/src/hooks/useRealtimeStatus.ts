import { useEffect, useRef, useState, useCallback } from 'react';
import { StatusData } from '../types';
import { getStatus } from '../services/api';
import { useSocket } from '../contexts/SocketContext';

interface UseRealtimeStatusOptions {
  pollInterval?: number;
}

interface UseRealtimeStatusReturn {
  status: StatusData;
  connected: boolean;
  isUsingWebSocket: boolean;
  error: string | null;
  forceRefresh: () => Promise<void>;
}

/**
 * Hook for real-time status updates with WebSocket and polling fallback
 *
 * Uses shared socket from SocketContext. Falls back to polling if socket
 * is not connected.
 */
export const useRealtimeStatus = ({
  pollInterval = 2000,
}: UseRealtimeStatusOptions = {}): UseRealtimeStatusReturn => {
  const { socket, connected } = useSocket();
  const [status, setStatus] = useState<StatusData>({});
  const [error, setError] = useState<string | null>(null);

  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Polling function
  const pollStatus = useCallback(async () => {
    try {
      const data = await getStatus();
      setStatus(data);
      setError(null);
    } catch (err) {
      console.error('Error polling status:', err);
      setError('Failed to fetch status');
    }
  }, []);

  // Start polling
  const startPolling = useCallback(() => {
    if (pollIntervalRef.current) return;

    console.log('Starting polling fallback');
    pollStatus();
    pollIntervalRef.current = setInterval(pollStatus, pollInterval);
  }, [pollStatus, pollInterval]);

  // Stop polling
  const stopPolling = useCallback(() => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
      console.log('Stopped polling');
    }
  }, []);

  // Set up socket event listeners
  useEffect(() => {
    if (!socket) {
      startPolling();
      return;
    }

    // Listen for status updates
    const handleStatusUpdate = (data: StatusData) => {
      console.debug('[WS] status_update received', Object.keys(data));
      setStatus(data);
      setError(null);
    };

    // Listen for download progress
    const handleDownloadProgress = (data: { book_id: string; progress: number; status: string }) => {
      console.debug('[WS] download_progress:', data.book_id, `${data.progress.toFixed(1)}%`);
      setStatus(prev => {
        const newStatus = { ...prev };

        if (newStatus.downloading?.[data.book_id]) {
          newStatus.downloading = {
            ...newStatus.downloading,
            [data.book_id]: {
              ...newStatus.downloading[data.book_id],
              progress: data.progress,
            },
          };
        }

        return newStatus;
      });
    };

    socket.on('status_update', handleStatusUpdate);
    socket.on('download_progress', handleDownloadProgress);

    // Request initial status when socket connects
    if (connected) {
      stopPolling();
      socket.emit('request_status');
    } else {
      startPolling();
    }

    return () => {
      socket.off('status_update', handleStatusUpdate);
      socket.off('download_progress', handleDownloadProgress);
    };
  }, [socket, connected, startPolling, stopPolling]);

  // Handle connection state changes
  useEffect(() => {
    if (connected) {
      stopPolling();
    } else {
      startPolling();
    }
  }, [connected, startPolling, stopPolling]);

  // Force refresh function
  const forceRefresh = useCallback(async () => {
    if (socket?.connected) {
      socket.emit('request_status');
    } else {
      await pollStatus();
    }
  }, [socket, pollStatus]);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  return {
    status,
    connected,
    isUsingWebSocket: connected,
    error,
    forceRefresh,
  };
};

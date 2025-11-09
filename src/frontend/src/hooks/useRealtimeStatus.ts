import { useEffect, useRef, useState, useCallback } from 'react';
import { io, Socket } from 'socket.io-client';
import { StatusData } from '../types';
import { getStatus } from '../services/api';

interface UseRealtimeStatusOptions {
  wsUrl: string;
  pollInterval?: number;
  reconnectAttempts?: number;
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
 * This hook attempts to connect via WebSocket first. If WebSocket connection
 * fails or disconnects, it automatically falls back to polling. It will
 * periodically retry WebSocket connections.
 */
export const useRealtimeStatus = ({
  wsUrl,
  pollInterval = 5000,
  reconnectAttempts = 3,
}: UseRealtimeStatusOptions): UseRealtimeStatusReturn => {
  const [status, setStatus] = useState<StatusData>({});
  const [connected, setConnected] = useState(false);
  const [isUsingWebSocket, setIsUsingWebSocket] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  const socketRef = useRef<Socket | null>(null);
  const pollIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const isConnectingRef = useRef(false);

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
    setIsUsingWebSocket(false);
    
    // Poll immediately
    pollStatus();
    
    // Then poll at intervals
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

  // Attempt to reconnect WebSocket
  const attemptReconnect = useCallback(() => {
    if (reconnectAttemptsRef.current >= reconnectAttempts) {
      console.log('Max reconnect attempts reached, using polling permanently');
      return;
    }

    reconnectAttemptsRef.current += 1;
    const delay = Math.min(1000 * Math.pow(2, reconnectAttemptsRef.current), 30000);
    
    console.log(`Attempting WebSocket reconnect ${reconnectAttemptsRef.current}/${reconnectAttempts} in ${delay}ms`);
    
    reconnectTimeoutRef.current = setTimeout(() => {
      if (!isConnectingRef.current && !socketRef.current?.connected) {
        initializeWebSocket();
      }
    }, delay);
  }, [reconnectAttempts]);

  // Initialize WebSocket connection
  const initializeWebSocket = useCallback(() => {
    if (isConnectingRef.current || socketRef.current?.connected) {
      return;
    }

    isConnectingRef.current = true;
    console.log('Initializing WebSocket connection to:', wsUrl);

    try {
      const socket = io(wsUrl, {
        transports: ['websocket', 'polling'],
        timeout: 5000,
        reconnection: true,
        reconnectionAttempts: 3,
        reconnectionDelay: 1000,
      });

      socketRef.current = socket;

      socket.on('connect', () => {
        console.log('WebSocket connected successfully');
        setConnected(true);
        setIsUsingWebSocket(true);
        setError(null);
        reconnectAttemptsRef.current = 0;
        isConnectingRef.current = false;
        
        // Stop polling when WebSocket connects
        stopPolling();
      });

      socket.on('disconnect', (reason: string) => {
        console.log('WebSocket disconnected:', reason);
        setConnected(false);
        setIsUsingWebSocket(false);
        isConnectingRef.current = false;
        
        // Start polling as fallback
        startPolling();
        
        // Attempt to reconnect WebSocket
        if (reason === 'io server disconnect' || reason === 'transport close') {
          attemptReconnect();
        }
      });

      socket.on('connect_error', (err: Error) => {
        console.error('WebSocket connection error:', err.message);
        setError(`WebSocket error: ${err.message}`);
        setConnected(false);
        setIsUsingWebSocket(false);
        isConnectingRef.current = false;
        
        // Start polling immediately on connection error
        startPolling();
        
        // Attempt to reconnect WebSocket
        attemptReconnect();
      });

      // Listen for status updates
      socket.on('status_update', (data: StatusData) => {
        setStatus(data);
        setError(null);
      });

      // Listen for real-time progress updates
      socket.on('download_progress', (data: { book_id: string; progress: number; status: string }) => {
        setStatus(prev => {
          const newStatus = { ...prev };
          if (newStatus.downloading?.[data.book_id]) {
            newStatus.downloading[data.book_id] = {
              ...newStatus.downloading[data.book_id],
              progress: data.progress,
            };
          }
          return newStatus;
        });
      });

      socket.on('error', (err: Error) => {
        console.error('WebSocket error:', err);
        setError('WebSocket error occurred');
      });

    } catch (err) {
      console.error('Failed to initialize WebSocket:', err);
      setError('Failed to initialize WebSocket');
      isConnectingRef.current = false;
      startPolling();
    }
  }, [wsUrl, stopPolling, startPolling, attemptReconnect]);

  // Force refresh function
  const forceRefresh = useCallback(async () => {
    if (socketRef.current?.connected) {
      // Request update via WebSocket
      socketRef.current.emit('request_status');
    } else {
      // Poll immediately
      await pollStatus();
    }
  }, [pollStatus]);

  // Initialize on mount
  useEffect(() => {
    // Try WebSocket first
    initializeWebSocket();
    
    // If WebSocket doesn't connect within 3 seconds, start polling
    const fallbackTimeout = setTimeout(() => {
      if (!socketRef.current?.connected) {
        console.log('WebSocket connection timeout, starting polling');
        startPolling();
      }
    }, 3000);

    // Cleanup
    return () => {
      clearTimeout(fallbackTimeout);
      
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      
      stopPolling();
      
      if (socketRef.current) {
        socketRef.current.disconnect();
        socketRef.current = null;
      }
    };
  }, [initializeWebSocket, startPolling, stopPolling]);

  return {
    status,
    connected,
    isUsingWebSocket,
    error,
    forceRefresh,
  };
};

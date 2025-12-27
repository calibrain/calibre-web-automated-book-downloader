import { createContext, useContext, useEffect, useRef, useState, ReactNode } from 'react';
import { io, Socket } from 'socket.io-client';

interface SocketContextValue {
  socket: Socket | null;
  connected: boolean;
}

const SocketContext = createContext<SocketContextValue>({ socket: null, connected: false });

export const useSocket = () => useContext(SocketContext);

interface SocketProviderProps {
  children: ReactNode;
}

export const SocketProvider = ({ children }: SocketProviderProps) => {
  const [connected, setConnected] = useState(false);
  const socketRef = useRef<Socket | null>(null);

  useEffect(() => {
    // In dev mode (port 5173), connect directly to backend to avoid Vite proxy issues
    const wsUrl = window.location.port === '5173'
      ? 'http://localhost:8084'
      : window.location.origin;

    console.log('SocketProvider: Connecting to', wsUrl);

    const socket = io(wsUrl, {
      path: '/socket.io',
      transports: ['polling', 'websocket'],
      withCredentials: false,
    });

    socketRef.current = socket;

    socket.on('connect', () => {
      console.log('✅ Socket connected via', socket.io.engine.transport.name);
      setConnected(true);
    });

    socket.on('disconnect', (reason) => {
      console.log('Socket disconnected:', reason);
      setConnected(false);
    });

    socket.on('connect_error', (err) => {
      console.error('Socket connection error:', err.message);
      setConnected(false);
    });

    return () => {
      console.log('SocketProvider: Disconnecting');
      socket.disconnect();
      socketRef.current = null;
    };
  }, []);

  return (
    <SocketContext.Provider value={{ socket: socketRef.current, connected }}>
      {children}
    </SocketContext.Provider>
  );
};

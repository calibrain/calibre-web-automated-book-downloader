import { useState, useCallback } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { useSocket } from '../contexts/SocketContext';
import { t } from '../i18n';
import { login, logout, checkAuth } from '../services/api';
import type { LoginCredentials } from '../types';
import { getReturnToFromSearch } from '../utils/authRedirect';
import { useMountEffect } from './useMountEffect';

interface UseAuthOptions {
  onLogoutSuccess?: () => void;
  showToast?: (message: string, type: 'info' | 'success' | 'error') => void;
}

interface UseAuthReturn {
  isAuthenticated: boolean;
  authRequired: boolean;
  authChecked: boolean;
  isAdmin: boolean;
  authMode: string;
  username: string | null;
  displayName: string | null;
  oidcButtonLabel: string | null;
  hideLocalAuth: boolean;
  oidcAutoRedirect: boolean;
  loginError: string | null;
  isLoggingIn: boolean;
  setIsAuthenticated: (value: boolean) => void;
  refreshAuth: () => Promise<void>;
  handleLogin: (credentials: LoginCredentials) => Promise<void>;
  handleLogout: () => Promise<void>;
}

export function useAuth(options: UseAuthOptions = {}): UseAuthReturn {
  const { onLogoutSuccess, showToast } = options;
  const location = useLocation();
  const navigate = useNavigate();
  const { socket } = useSocket();
  const postLoginPath = getReturnToFromSearch(location.search);

  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [authRequired, setAuthRequired] = useState(true);
  const [authChecked, setAuthChecked] = useState(false);
  const [isAdmin, setIsAdmin] = useState(false);
  const [authMode, setAuthMode] = useState('none');
  const [username, setUsername] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState<string | null>(null);
  const [oidcButtonLabel, setOidcButtonLabel] = useState<string | null>(null);
  const [hideLocalAuth, setHideLocalAuth] = useState(false);
  const [oidcAutoRedirect, setOidcAutoRedirect] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState(false);

  const applyAuthResponse = useCallback((response: Awaited<ReturnType<typeof checkAuth>>) => {
    setAuthRequired(response.auth_required !== false);
    setIsAuthenticated(response.authenticated || false);
    setIsAdmin(response.is_admin || false);
    setAuthMode(response.auth_mode || 'none');
    setUsername(response.username || null);
    setDisplayName(response.display_name || null);
    setOidcButtonLabel(response.oidc_button_label || null);
    setHideLocalAuth(response.hide_local_auth || false);
    setOidcAutoRedirect(response.oidc_auto_redirect || false);
  }, []);

  const refreshSocketSession = useCallback(() => {
    if (!socket) {
      return;
    }
    // Flask-SocketIO reads session state from the socket handshake context.
    // Reconnect after auth state changes so socket events use the latest session.
    socket.disconnect();
    socket.connect();
  }, [socket]);

  // Check authentication on mount
  useMountEffect(() => {
    const verifyAuth = async () => {
      try {
        applyAuthResponse(await checkAuth());
      } catch (error) {
        console.error('Auth check failed:', error);
        setAuthRequired(true);
        setIsAuthenticated(false);
        setIsAdmin(false);
      } finally {
        setAuthChecked(true);
      }
    };
    void verifyAuth();
  });

  // Re-sync auth when returning to the tab, so role/session changes in
  // another tab/profile don't leave stale local auth state.
  useMountEffect(() => {
    const verifyAuthOnFocus = async () => {
      try {
        applyAuthResponse(await checkAuth());
      } catch (error) {
        console.error('Auth re-check failed:', error);
      }
    };

    const handleFocus = () => {
      void verifyAuthOnFocus();
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void verifyAuthOnFocus();
      }
    };

    window.addEventListener('focus', handleFocus);
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      window.removeEventListener('focus', handleFocus);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  });

  const refreshAuth = useCallback(async () => {
    try {
      applyAuthResponse(await checkAuth());
    } catch (error) {
      console.error('Auth refresh failed:', error);
    }
  }, [applyAuthResponse]);

  const handleLogin = useCallback(
    async (credentials: LoginCredentials) => {
      setIsLoggingIn(true);
      setLoginError(null);
      try {
        const response = await login(credentials);
        if (response.success) {
          // Re-check auth to get updated session state
          applyAuthResponse(await checkAuth());
          refreshSocketSession();
          setLoginError(null);
          void navigate(postLoginPath, { replace: true });
        } else {
          setLoginError(response.error ? t(response.error) : t('Login failed'));
        }
      } catch (error) {
        if (error instanceof Error) {
          setLoginError(error.message || t('Login failed'));
        } else {
          setLoginError(t('Login failed'));
        }
      } finally {
        setIsLoggingIn(false);
      }
    },
    [navigate, applyAuthResponse, postLoginPath, refreshSocketSession],
  );

  const handleLogout = useCallback(async () => {
    try {
      const { logout_url } = await logout();
      if (logout_url?.startsWith('https://') || logout_url?.startsWith('http://')) {
        window.location.href = logout_url;
        return;
      }
      try {
        // Reload auth settings after session clear so /login has current auth-mode UI config.
        applyAuthResponse(await checkAuth());
      } catch (error) {
        console.error('Post-logout auth refresh failed:', error);
        setIsAuthenticated(false);
        setIsAdmin(false);
        setUsername(null);
        setDisplayName(null);
      }
      refreshSocketSession();
      onLogoutSuccess?.();
      void navigate('/login', { replace: true });
    } catch (error) {
      console.error('Logout failed:', error);
      showToast?.(t('Logout failed'), 'error');
    }
  }, [navigate, onLogoutSuccess, refreshSocketSession, showToast, applyAuthResponse]);

  return {
    isAuthenticated,
    authRequired,
    authChecked,
    isAdmin,
    authMode,
    username,
    displayName,
    oidcButtonLabel,
    hideLocalAuth,
    oidcAutoRedirect,
    loginError,
    isLoggingIn,
    setIsAuthenticated,
    refreshAuth,
    handleLogin,
    handleLogout,
  };
}

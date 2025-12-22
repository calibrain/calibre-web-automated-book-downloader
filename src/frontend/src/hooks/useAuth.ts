import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { LoginCredentials } from '../types';
import { login, logout, checkAuth } from '../services/api';

interface UseAuthOptions {
  onLogoutSuccess?: () => void;
  showToast?: (message: string, type: 'info' | 'success' | 'error') => void;
}

interface UseAuthReturn {
  isAuthenticated: boolean;
  authRequired: boolean;
  authChecked: boolean;
  loginError: string | null;
  isLoggingIn: boolean;
  setIsAuthenticated: (value: boolean) => void;
  handleLogin: (credentials: LoginCredentials) => Promise<void>;
  handleLogout: () => Promise<void>;
}

export function useAuth(options: UseAuthOptions = {}): UseAuthReturn {
  const { onLogoutSuccess, showToast } = options;
  const navigate = useNavigate();

  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(false);
  const [authRequired, setAuthRequired] = useState<boolean>(true);
  const [authChecked, setAuthChecked] = useState<boolean>(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState<boolean>(false);

  // Check authentication on mount
  useEffect(() => {
    const verifyAuth = async () => {
      try {
        const response = await checkAuth();
        const authenticated = response.authenticated || false;
        const authIsRequired = response.auth_required !== false;

        setAuthRequired(authIsRequired);
        setIsAuthenticated(authenticated);
      } catch (error) {
        console.error('Auth check failed:', error);
        setAuthRequired(true);
        setIsAuthenticated(false);
      } finally {
        setAuthChecked(true);
      }
    };
    verifyAuth();
  }, []);

  const handleLogin = useCallback(async (credentials: LoginCredentials) => {
    setIsLoggingIn(true);
    setLoginError(null);
    try {
      const response = await login(credentials);
      if (response.success) {
        setIsAuthenticated(true);
        setLoginError(null);
        navigate('/', { replace: true });
      } else {
        setLoginError(response.error || 'Login failed');
      }
    } catch (error) {
      if (error instanceof Error) {
        setLoginError(error.message || 'Login failed');
      } else {
        setLoginError('Login failed');
      }
    } finally {
      setIsLoggingIn(false);
    }
  }, [navigate]);

  const handleLogout = useCallback(async () => {
    try {
      await logout();
      setIsAuthenticated(false);
      onLogoutSuccess?.();
      navigate('/login', { replace: true });
    } catch (error) {
      console.error('Logout failed:', error);
      showToast?.('Logout failed', 'error');
    }
  }, [navigate, onLogoutSuccess, showToast]);

  return {
    isAuthenticated,
    authRequired,
    authChecked,
    loginError,
    isLoggingIn,
    setIsAuthenticated,
    handleLogin,
    handleLogout,
  };
}

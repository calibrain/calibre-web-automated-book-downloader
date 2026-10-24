import type { FormEvent, KeyboardEvent } from 'react';
import { useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { useMountEffect } from '../hooks/useMountEffect';
import { t } from '../i18n';
import type { LoginCredentials } from '../types';
import { buildOidcLoginUrl } from '../utils/authRedirect';
import { withBasePath } from '../utils/basePath';

interface LoginFormProps {
  onSubmit: (credentials: LoginCredentials) => void;
  error?: string | null;
  isLoading?: boolean;
  autoFocus?: boolean;
  authMode?: string;
  oidcButtonLabel?: string | null;
  hideLocalAuth?: boolean;
  oidcAutoRedirect?: boolean;
}

const EyeIcon = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    fill="none"
    viewBox="0 0 24 24"
    strokeWidth={1.5}
    stroke="currentColor"
    className="h-5 w-5"
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z"
    />
    <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
  </svg>
);

const EyeSlashIcon = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    fill="none"
    viewBox="0 0 24 24"
    strokeWidth={1.5}
    stroke="currentColor"
    className="h-5 w-5"
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88"
    />
  </svg>
);

const PasswordLoginForm = ({
  onSubmit,
  isLoading,
  shouldFocusOnMount,
}: {
  onSubmit: (e: FormEvent<HTMLFormElement>) => void;
  isLoading: boolean;
  shouldFocusOnMount: boolean;
}) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [rememberMe, setRememberMe] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const usernameRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);

  useMountEffect(() => {
    if (shouldFocusOnMount) {
      usernameRef.current?.focus();
    }
  });

  const handleUsernameKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      passwordRef.current?.focus();
    }
  };

  return (
    <form
      method="post"
      action={withBasePath('/api/login')}
      autoComplete="on"
      id="login-form"
      name="login"
      data-form-type="login"
      onSubmit={onSubmit}
    >
      <div className="mb-4">
        <label htmlFor="username" className="mb-2 block text-sm font-medium">
          {t('Username')}
        </label>
        <input
          ref={usernameRef}
          type="text"
          id="username"
          name="username"
          aria-label={t('Username')}
          autoComplete="username"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          inputMode="text"
          enterKeyHint="next"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          onKeyDown={handleUsernameKeyDown}
          disabled={isLoading}
          className="w-full rounded-lg border px-4 py-2.5 transition-colors focus:ring-2 focus:ring-sky-500 focus:outline-hidden disabled:cursor-not-allowed disabled:opacity-50"
          style={{
            backgroundColor: 'var(--input-background)',
            borderColor: 'var(--border-color)',
            color: 'var(--text-color)',
          }}
          required
        />
      </div>

      <div className="mb-4">
        <label htmlFor="password" className="mb-2 block text-sm font-medium">
          {t('Password')}
        </label>
        <div className="relative">
          <input
            ref={passwordRef}
            type={showPassword ? 'text' : 'password'}
            id="password"
            name="password"
            aria-label={t('Password')}
            autoComplete="current-password"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            inputMode="text"
            enterKeyHint="go"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={isLoading}
            className="w-full rounded-lg border px-4 py-2.5 pr-10 transition-colors focus:ring-2 focus:ring-sky-500 focus:outline-hidden disabled:cursor-not-allowed disabled:opacity-50"
            style={{
              backgroundColor: 'var(--input-background)',
              borderColor: 'var(--border-color)',
              color: 'var(--text-color)',
            }}
            required
          />
          <button
            type="button"
            onClick={() => setShowPassword((current) => !current)}
            disabled={isLoading}
            className="hover-action absolute top-1/2 right-2 -translate-y-1/2 transform rounded-full p-1.5 transition-colors disabled:cursor-not-allowed disabled:opacity-50"
            aria-label={showPassword ? t('Hide password') : t('Show password')}
          >
            {showPassword ? <EyeSlashIcon /> : <EyeIcon />}
          </button>
        </div>
      </div>

      <div className="mb-6 flex items-center">
        <input
          type="checkbox"
          id="remember-me"
          name="remember_me"
          checked={rememberMe}
          onChange={(event) => setRememberMe(event.target.checked)}
          disabled={isLoading}
          aria-label={t('Remember me for 7 days')}
          className="h-4 w-4 rounded-sm accent-sky-900 focus:ring-2 focus:ring-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
          style={{ borderColor: 'var(--border-color)' }}
        />
        <label htmlFor="remember-me" className="ml-2 text-sm">
          {t('Remember me for 7 days')}
        </label>
      </div>

      <button
        type="submit"
        name="submit"
        disabled={isLoading}
        className="w-full rounded-lg bg-sky-700 px-4 py-2.5 font-medium text-white transition-colors hover:bg-sky-800 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-sky-700"
        aria-label={t('Sign in')}
      >
        {isLoading ? (
          <span className="flex items-center justify-center">
            <svg
              className="mr-3 -ml-1 h-5 w-5 animate-spin text-white"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
              />
            </svg>
            {t('Signing in...')}
          </span>
        ) : (
          t('Sign In')
        )}
      </button>
    </form>
  );
};

export const LoginForm = ({
  onSubmit,
  error = null,
  isLoading = false,
  autoFocus = true,
  authMode,
  oidcButtonLabel,
  hideLocalAuth = false,
  oidcAutoRedirect = false,
}: LoginFormProps) => {
  const isOidc = authMode === 'oidc';
  const [showPasswordLogin, setShowPasswordLogin] = useState(false);
  const [lastAutoExpandPasswordLoginKey, setLastAutoExpandPasswordLoginKey] = useState('');
  const [searchParams] = useSearchParams();
  const oidcError = searchParams.get('oidc_error');
  const oidcLoginUrl = buildOidcLoginUrl(searchParams.toString());
  const autoExpandPasswordLoginKey = isOidc && error ? error : '';

  // React recommends adjusting state during render in rare cases like this,
  // rather than mirroring props in an effect. This preserves the previous
  // auto-open-on-new-error behavior while still letting the user hide the form.
  if (autoExpandPasswordLoginKey !== lastAutoExpandPasswordLoginKey) {
    setLastAutoExpandPasswordLoginKey(autoExpandPasswordLoginKey);
    if (autoExpandPasswordLoginKey) {
      setShowPasswordLogin(true);
    }
  }

  useMountEffect(() => {
    if (oidcAutoRedirect && isOidc && !error && !oidcError) {
      window.location.href = oidcLoginUrl;
    }
  });

  const handleSubmit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const formData = new FormData(e.currentTarget);
    const usernameEntry = formData.get('username');
    const passwordEntry = formData.get('password');
    const usernameValue = typeof usernameEntry === 'string' ? usernameEntry.trim() : '';
    const passwordValue = typeof passwordEntry === 'string' ? passwordEntry : '';

    if (usernameValue && passwordValue && !isLoading) {
      onSubmit({
        username: usernameValue,
        password: passwordValue,
        remember_me: formData.has('remember_me'),
      });
    }
  };

  const displayError = (oidcError && t(oidcError)) || error;

  return (
    <div>
      {displayError && (
        <div className="mb-4 rounded-lg bg-red-600 p-3 text-sm text-white">{displayError}</div>
      )}

      {isOidc ? (
        <>
          <a
            href={oidcLoginUrl}
            className="block w-full rounded-lg bg-sky-700 px-4 py-2.5 text-center font-medium text-white transition-colors hover:bg-sky-800"
          >
            {oidcButtonLabel || t('Sign in with OIDC')}
          </a>

          {!hideLocalAuth && (
            <>
              <div className="mt-5 mb-2 flex items-center">
                <div className="flex-1 border-t" style={{ borderColor: 'var(--border-color)' }} />
                <button
                  type="button"
                  onClick={() => setShowPasswordLogin((prev) => !prev)}
                  className="px-3 text-sm opacity-60 transition-opacity hover:opacity-100"
                >
                  {showPasswordLogin ? t('Hide') : t('Use password')}
                </button>
                <div className="flex-1 border-t" style={{ borderColor: 'var(--border-color)' }} />
              </div>

              {showPasswordLogin && (
                <div className="pt-2">
                  <PasswordLoginForm
                    onSubmit={handleSubmit}
                    isLoading={isLoading}
                    shouldFocusOnMount={true}
                  />
                </div>
              )}
            </>
          )}
        </>
      ) : (
        <PasswordLoginForm
          onSubmit={handleSubmit}
          isLoading={isLoading}
          shouldFocusOnMount={autoFocus}
        />
      )}
    </div>
  );
};

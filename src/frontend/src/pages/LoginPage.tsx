import { LoginForm } from '../components/LoginForm';
import { t } from '../i18n';
import type { LoginCredentials } from '../types';
import { withBasePath } from '../utils/basePath';

interface LoginPageProps {
  onLogin: (credentials: LoginCredentials) => void;
  error: string | null;
  isLoading: boolean;
  authMode?: string;
  oidcButtonLabel?: string | null;
  hideLocalAuth?: boolean;
  oidcAutoRedirect?: boolean;
}

export const LoginPage = ({
  onLogin,
  error,
  isLoading,
  authMode,
  oidcButtonLabel,
  hideLocalAuth,
  oidcAutoRedirect,
}: LoginPageProps) => {
  const logoUrl = withBasePath('/logo.png');

  return (
    <div
      className="flex min-h-screen items-center justify-center px-4 py-8"
      style={{ backgroundColor: 'var(--background-color)', color: 'var(--text-color)' }}
    >
      <div className="w-full max-w-md">
        <div
          className="rounded-lg border p-6 shadow-2xl"
          style={{
            backgroundColor: 'var(--card-background)',
            borderColor: 'var(--border-color)',
            color: 'var(--text-color)',
          }}
        >
          <div className="mb-5 text-center">
            <img src={logoUrl} alt={t('Logo')} className="mx-auto h-12 w-12" />
          </div>
          <LoginForm
            onSubmit={onLogin}
            error={error}
            isLoading={isLoading}
            authMode={authMode}
            oidcButtonLabel={oidcButtonLabel}
            hideLocalAuth={hideLocalAuth}
            oidcAutoRedirect={oidcAutoRedirect}
          />
        </div>
      </div>
    </div>
  );
};

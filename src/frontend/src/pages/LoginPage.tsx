import { LoginForm } from '../components/LoginForm';
import { LoginCredentials } from '../types';

interface LoginPageProps {
  onLogin: (credentials: LoginCredentials) => void;
  error: string | null;
  isLoading: boolean;
}

export const LoginPage = ({ onLogin, error, isLoading }: LoginPageProps) => {
  return (
    <div
      className="min-h-screen flex items-center justify-center px-4 py-8"
      style={{ backgroundColor: 'var(--background-color)', color: 'var(--text-color)' }}
    >
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <img src="/logo.png" alt="Logo" className="mx-auto mb-6 w-20 h-20" />
          <h1 className="text-2xl font-semibold">Sign in to continue</h1>
        </div>
        <div
          className="rounded-lg shadow-2xl p-8 border"
          style={{
            backgroundColor: 'var(--card-background)',
            borderColor: 'var(--border-color)',
            color: 'var(--text-color)',
          }}
        >
          <LoginForm onSubmit={onLogin} error={error} isLoading={isLoading} />
        </div>
      </div>
    </div>
  );
};



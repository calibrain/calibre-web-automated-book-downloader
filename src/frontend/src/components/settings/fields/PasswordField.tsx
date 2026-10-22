import { useState } from 'react';

import { t } from '../../../i18n';
import type { PasswordFieldConfig } from '../../../types/settings';

interface PasswordFieldProps {
  field: PasswordFieldConfig;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

export const PasswordField = ({ field, value, onChange, disabled }: PasswordFieldProps) => {
  const [showPassword, setShowPassword] = useState(false);
  // disabled prop is already computed by SettingsContent.getDisabledState()
  const isDisabled = disabled ?? false;

  return (
    <div className="relative">
      <input
        type={showPassword ? 'text' : 'password'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={field.placeholder}
        aria-label={field.label || field.placeholder || t('Password field')}
        disabled={isDisabled}
        className="w-full rounded-lg border border-(--border-muted) bg-(--bg-soft) px-3 py-2 pr-10 text-sm transition-colors focus:border-sky-500 focus:ring-2 focus:ring-sky-500/50 focus:outline-hidden disabled:cursor-not-allowed disabled:opacity-60"
      />
      <button
        type="button"
        onClick={() => setShowPassword(!showPassword)}
        disabled={isDisabled}
        className="absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 transition-colors hover:bg-(--hover-action) disabled:cursor-not-allowed disabled:opacity-60"
        tabIndex={-1}
        aria-label={showPassword ? t('Hide password') : t('Show password')}
      >
        {showPassword ? (
          <svg
            className="h-4 w-4 opacity-60"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.5}
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88"
            />
          </svg>
        ) : (
          <svg
            className="h-4 w-4 opacity-60"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.5}
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z"
            />
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
            />
          </svg>
        )}
      </button>
    </div>
  );
};

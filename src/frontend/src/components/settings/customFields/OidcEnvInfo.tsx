import { t } from '../../../i18n';
import type { CustomSettingsFieldRendererProps } from './types';

export const OidcEnvInfo = (_props: CustomSettingsFieldRendererProps) => {
  return (
    <div className="overflow-hidden rounded-lg border border-(--border-muted)">
      <div
        className="border-b border-(--border-muted) px-3 py-1.5 text-xs font-medium opacity-60"
        style={{ background: 'var(--bg-soft)' }}
      >
        docker-compose.yml
      </div>
      <pre className="overflow-x-auto px-3 py-3 text-xs" style={{ background: 'var(--bg-soft)' }}>
        <code>
          <span className="opacity-60">environment:</span>
          {'\n'}
          {'  '}- <span className="text-blue-400">HIDE_LOCAL_AUTH</span>=
          <span className="text-green-400">true</span>
          {'    '}
          <span className="opacity-40"># {t('Hide the local login form')}</span>
          {'\n'}
          {'  '}- <span className="text-blue-400">DISABLE_LOCAL_AUTH</span>=
          <span className="text-green-400">true</span>{' '}
          <span className="opacity-40"># {t('Disable username/password login')}</span>
          {'\n'}
          {'  '}- <span className="text-blue-400">OIDC_AUTO_REDIRECT</span>=
          <span className="text-green-400">true</span>
          {'  '}
          <span className="opacity-40"># {t('Skip login page, redirect straight to OIDC')}</span>
        </code>
      </pre>
    </div>
  );
};

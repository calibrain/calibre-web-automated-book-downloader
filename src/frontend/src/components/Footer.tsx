import { t } from '../i18n';
import { shortBuildId } from '../utils/buildVersion';

interface FooterProps {
  buildVersion?: string;
  releaseVersion?: string;
  debug?: boolean;
}

export const Footer = ({ buildVersion, releaseVersion, debug }: FooterProps) => {
  // Determine version display - show "dev" if no version is set
  const versionDisplay = releaseVersion && releaseVersion !== 'N/A' ? releaseVersion : 'dev';

  const buildId = shortBuildId(buildVersion);

  return (
    <footer
      className="mt-8 py-4"
      style={{
        paddingBottom: 'calc(1rem + env(safe-area-inset-bottom))',
      }}
    >
      <div className="mx-auto flex max-w-7xl items-baseline justify-center gap-2 px-4 sm:px-6 lg:px-8">
        <a
          href="https://github.com/calibrain/shelfmark"
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm font-medium opacity-70 transition-opacity hover:opacity-100"
        >
          Shelfmark
        </a>
        <span
          className="text-xs font-normal opacity-40"
          title={
            buildVersion && buildVersion !== 'N/A'
              ? t('Build: {version}', { version: buildVersion })
              : undefined
          }
        >
          {versionDisplay}
          {buildId && ` (${buildId})`}
        </span>
        {debug && (
          <span
            className="rounded-sm px-1.5 py-0.5 text-xs opacity-60"
            style={{ background: 'var(--border-muted)' }}
          >
            {t('Debug')}
          </span>
        )}
      </div>
    </footer>
  );
};

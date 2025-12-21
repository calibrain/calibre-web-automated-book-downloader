interface EnvLockBadgeProps {
  className?: string;
}

export const EnvLockBadge = ({ className = '' }: EnvLockBadgeProps) => (
  <span
    className={`inline-flex items-center gap-1 px-1.5 py-0.5
                text-[10px] font-medium uppercase tracking-wide
                bg-gray-200 dark:bg-gray-700
                text-gray-600 dark:text-gray-400
                rounded ${className}`}
    title="This setting is controlled by an environment variable"
  >
    <svg
      className="w-3 h-3"
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 20 20"
      fill="currentColor"
    >
      <path
        fillRule="evenodd"
        d="M10 1a4.5 4.5 0 00-4.5 4.5V9H5a2 2 0 00-2 2v6a2 2 0 002 2h10a2 2 0 002-2v-6a2 2 0 00-2-2h-.5V5.5A4.5 4.5 0 0010 1zm3 8V5.5a3 3 0 10-6 0V9h6z"
        clipRule="evenodd"
      />
    </svg>
    ENV
  </span>
);

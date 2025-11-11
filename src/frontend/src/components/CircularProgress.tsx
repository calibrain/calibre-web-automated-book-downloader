interface CircularProgressProps {
  progress?: number;
  size?: number;
}

/**
 * Circular progress indicator component
 * Displays a circular SVG progress ring that fills based on the progress percentage
 */
export const CircularProgress = ({ progress, size = 16 }: CircularProgressProps) => {
  const radius = (size - 2) / 2;
  const circumference = 2 * Math.PI * radius;
  const progressValue = progress ?? 0;
  const strokeDashoffset = circumference - (progressValue / 100) * circumference;

  return (
    <svg width={size} height={size} className="transform -rotate-90">
      {/* Background circle */}
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        opacity="0.3"
      />
      {/* Progress circle */}
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeDasharray={circumference}
        strokeDashoffset={strokeDashoffset}
        strokeLinecap="round"
        style={{ transition: 'stroke-dashoffset 0.3s ease' }}
      />
    </svg>
  );
};

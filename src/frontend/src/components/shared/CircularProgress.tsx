interface CircularProgressProps {
  progress?: number;
  size?: number;
  className?: string;
}

export const CircularProgress = ({ progress, size = 16, className }: CircularProgressProps) => {
  const radius = (size - 2) / 2;
  const circumference = 2 * Math.PI * radius;
  const progressValue = progress ?? 0;
  const strokeDashoffset = circumference - (progressValue / 100) * circumference;
  const svgClassName = className ? `transform -rotate-90 ${className}` : 'transform -rotate-90';

  return (
    <svg width={size} height={size} className={svgClassName}>
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        opacity="0.3"
      />
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

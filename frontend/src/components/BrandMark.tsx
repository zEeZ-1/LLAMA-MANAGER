export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 40 40"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="M6 30 20 6l14 24M12 21h16M16 30l4-7 4 7"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <circle cx="6" cy="30" r="3" fill="currentColor" />
      <circle cx="34" cy="30" r="3" fill="currentColor" />
      <circle cx="20" cy="6" r="3" fill="currentColor" />
    </svg>
  );
}

import type { ReactNode } from "react";

// Inline SVG icons on a 16 px grid. They are decoration: the words next to them carry the meaning,
// so each is hidden from assistive technology. Each icon has its own silhouette, so the verdict
// states differ in shape as well as in color.

export interface IconProps {
  size?: number;
  className?: string;
}

function Svg({ size = 14, className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      {children}
    </svg>
  );
}

/** The brand mark: a check in a rounded square, as in the favicon. */
export const IconMark = (p: IconProps) => (
  <Svg {...p}>
    <rect x="1.3" y="1.3" width="13.4" height="13.4" rx="3.4" />
    <path d="M4.6 8.4l2.4 2.4 4.4-4.6" />
  </Svg>
);

/** Met: a check in a circle. */
export const IconCheck = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6.6" />
    <path d="M4.9 8.3l2.2 2.2 4-4.4" />
  </Svg>
);

/** Not met: a cross in a square. */
export const IconCross = (p: IconProps) => (
  <Svg {...p}>
    <rect x="1.8" y="1.8" width="12.4" height="12.4" rx="2.2" />
    <path d="M5.4 5.4l5.2 5.2M10.6 5.4l-5.2 5.2" />
  </Svg>
);

/** Insufficient: a warning triangle. */
export const IconWarning = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 1.9L14.6 13.6H1.4z" />
    <path d="M8 6.2v3.4M8 11.6v.2" />
  </Svg>
);

/** Invalidated: a circle with a bar through it. */
export const IconBan = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6.6" />
    <path d="M3.3 12.7L12.7 3.3" />
  </Svg>
);

/** False approval: an octagon with an exclamation mark. */
export const IconAlert = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5.2 1.6h5.6l3.6 3.6v5.6l-3.6 3.6H5.2l-3.6-3.6V5.2z" />
    <path d="M8 4.8v4.1M8 11v.2" strokeWidth="1.9" />
  </Svg>
);

/** Not applicable: a diamond with a dash. */
export const IconNotApplicable = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 1.6l6.4 6.4L8 14.4 1.6 8z" />
    <path d="M5.2 8h5.6" />
  </Svg>
);

/** A tick, for "correct". */
export const IconTick = (p: IconProps) => (
  <Svg {...p} className={p.className}>
    <path d="M3 8.6l3.3 3.3L13 4.8" strokeWidth="2" />
  </Svg>
);

/** A plain cross, for "incorrect". */
export const IconClose = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" strokeWidth="2" />
  </Svg>
);

export const IconChevron = ({ open, ...p }: IconProps & { open: boolean }) => (
  <Svg {...p} size={p.size ?? 12}>
    <path d={open ? "M3 5.5l5 5 5-5" : "M5.5 3l5 5-5 5"} strokeWidth="1.9" />
  </Svg>
);

export const IconArrowRight = (p: IconProps) => (
  <Svg {...p}>
    <path d="M2.5 8h11M9 3.5L13.5 8 9 12.5" />
  </Svg>
);

export const IconExternal = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9.5 2.5h4v4M13.5 2.5L7.5 8.5M12 9.5v3.2a.8.8 0 01-.8.8H3.3a.8.8 0 01-.8-.8V4.8a.8.8 0 01.8-.8h3.2" />
  </Svg>
);

/** The repository link: angle brackets around a slash. */
export const IconCode = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5.2 4L1.6 8l3.6 4M10.8 4L14.4 8l-3.6 4M9.2 2.8L6.8 13.2" />
  </Svg>
);

export const IconInfo = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6.6" />
    <path d="M8 7.2v4M8 4.9v.2" />
  </Svg>
);

/** Shown to the model: an open eye. */
export const IconEye = (p: IconProps) => (
  <Svg {...p}>
    <path d="M1.2 8S3.6 3.4 8 3.4 14.8 8 14.8 8 12.4 12.6 8 12.6 1.2 8 1.2 8z" />
    <circle cx="8" cy="8" r="2" />
  </Svg>
);

/** Not shown to the model: an eye with a bar through it. */
export const IconEyeOff = (p: IconProps) => (
  <Svg {...p}>
    <path d="M1.2 8S3.6 3.4 8 3.4 14.8 8 14.8 8 12.4 12.6 8 12.6 1.2 8 1.2 8z" />
    <path d="M2.6 13.4L13.4 2.6" />
  </Svg>
);

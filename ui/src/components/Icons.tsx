// Stroke icons. Always decorative: the status word next to them carries the meaning.

type IconProps = { size?: number };

const base = (size: number) => ({
  width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
  strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true, focusable: false,
});

export const CheckCircle = ({ size = 22 }: IconProps) => (
  <svg {...base(size)} strokeWidth={2.4}><circle cx="12" cy="12" r="9.5" /><path d="M7.5 12.5l3 3 6-7" /></svg>
);
export const Triangle = ({ size = 22 }: IconProps) => (
  <svg {...base(size)} strokeWidth={2.4}><path d="M12 3 2 21h20L12 3z" /><path d="M12 10v5" /><path d="M12 18h.01" /></svg>
);
export const CrossCircle = ({ size = 22 }: IconProps) => (
  <svg {...base(size)} strokeWidth={2.4}><circle cx="12" cy="12" r="9.5" /><path d="M9 9l6 6M15 9l-6 6" /></svg>
);
export const Clock = ({ size = 22 }: IconProps) => (
  <svg {...base(size)} strokeWidth={2.2}><circle cx="12" cy="12" r="9.5" /><path d="M12 7v5l3 2" /></svg>
);
export const Pencil = ({ size = 22 }: IconProps) => (
  <svg {...base(size)} strokeWidth={2.2}><path d="M4 20h4L19 9l-4-4L4 16v4z" /><path d="M13.5 6.5l4 4" /></svg>
);
export const ChevronLeft = ({ size = 22 }: IconProps) => (
  <svg {...base(size)} strokeWidth={2.2}><path d="M15 5l-7 7 7 7" /></svg>
);
export const Check = ({ size = 52 }: IconProps) => (
  <svg {...base(size)} strokeWidth={2}><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
);

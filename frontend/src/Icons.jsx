// Lucide-style 24x24 stroke icons used across the app (17px in nav, per design).

const base = (size, sw) => ({
  width: size,
  height: size,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: sw,
  strokeLinecap: "round",
  strokeLinejoin: "round",
  style: { flex: "none" },
});

export const HomeIcon = ({ size = 17 }) => (
  <svg {...base(size, 1.7)}>
    <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    <path d="M9 22V12h6v10" />
  </svg>
);

export const CalendarIcon = ({ size = 17 }) => (
  <svg {...base(size, 1.7)}>
    <rect x="3" y="4" width="18" height="18" rx="4" />
    <path d="M16 2v4" />
    <path d="M8 2v4" />
    <path d="M3 10h18" />
  </svg>
);

export const ActivityIcon = ({ size = 17 }) => (
  <svg {...base(size, 1.7)}>
    <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
  </svg>
);

export const PollsIcon = ({ size = 17 }) => (
  <svg {...base(size, 1.7)}>
    <path d="M18 20V10" />
    <path d="M12 20V4" />
    <path d="M6 20v-6" />
  </svg>
);

export const ChevronLeft = ({ size = 15, sw = 2 }) => (
  <svg {...base(size, sw)}>
    <path d="m15 18-6-6 6-6" />
  </svg>
);

export const ChevronRight = ({ size = 15, sw = 2 }) => (
  <svg {...base(size, sw)}>
    <path d="m9 18 6-6-6-6" />
  </svg>
);

export const PlusIcon = ({ size = 13 }) => (
  <svg {...base(size, 2)}>
    <path d="M5 12h14" />
    <path d="M12 5v14" />
  </svg>
);

export const CheckIcon = ({ size = 14, color, sw = 2.4 }) => (
  <svg {...base(size, sw)} stroke={color || "currentColor"}>
    <path d="M20 6 9 17l-5-5" />
  </svg>
);

export const XIcon = ({ size = 14 }) => (
  <svg {...base(size, 2)}>
    <path d="M18 6 6 18" />
    <path d="m6 6 12 12" />
  </svg>
);

export const SearchIcon = ({ size = 14 }) => (
  <svg {...base(size, 1.8)} stroke="#a49c8c">
    <circle cx="11" cy="11" r="8" />
    <path d="m21 21-4.3-4.3" />
  </svg>
);

export const BellIcon = ({ size = 17 }) => (
  <svg {...base(size, 1.7)} stroke="#5c564b">
    <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
    <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
  </svg>
);

export const GearIcon = ({ size = 15 }) => (
  <svg {...base(size, 1.7)}>
    <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

export const SendIcon = ({ size = 15 }) => (
  <svg {...base(size, 1.9)} stroke="#F7F2EA">
    <path d="m22 2-7 20-4-9-9-4Z" />
    <path d="M22 2 11 13" />
  </svg>
);

export const ClockIcon = ({ size = 15 }) => (
  <svg {...base(size, 1.7)}>
    <circle cx="12" cy="12" r="10" />
    <path d="M12 6v6l4 2" />
  </svg>
);

export const PinIcon = ({ size = 15 }) => (
  <svg {...base(size, 1.7)}>
    <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z" />
    <circle cx="12" cy="10" r="3" />
  </svg>
);

export const UsersIcon = ({ size = 15 }) => (
  <svg {...base(size, 1.7)}>
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
    <path d="M16 3.13a4 4 0 0 1 0 7.75" />
  </svg>
);

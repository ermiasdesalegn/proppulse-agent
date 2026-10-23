/** Small line icons (24px grid, 1.75 stroke) so navigation and numbers can be told apart at a glance. */
const PATHS: Record<string, React.ReactNode> = {
  overview: <><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>,
  crm: <><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20c.6-3.4 3.2-5.5 6.5-5.5s5.9 2.1 6.5 5.5" /><path d="M16 4.7a3.5 3.5 0 0 1 0 6.6M18.5 14.9c1.6.8 2.7 2.4 3 4.6" /></>,
  board: <><rect x="3" y="3" width="5" height="18" rx="1.5" /><rect x="10" y="3" width="5" height="12" rx="1.5" /><rect x="17" y="3" width="4" height="8" rx="1.5" /></>,
  brain: <><path d="M12 4a3.5 3.5 0 0 0-6.4 1.9A3.6 3.6 0 0 0 4 12.5 3.6 3.6 0 0 0 6.5 19 3.5 3.5 0 0 0 12 20" /><path d="M12 4a3.5 3.5 0 0 1 6.4 1.9A3.6 3.6 0 0 1 20 12.5a3.6 3.6 0 0 1-2.5 6.5A3.5 3.5 0 0 1 12 20V4Z" /><path d="M9 9.5h1.5M13.5 14.5H15" /></>,
  map: <><path d="M9 4 3 6.5v13.5l6-2.5 6 2.5 6-2.5V4l-6 2.5L9 4Z" /><path d="M9 4v13.5M15 6.5V20" /></>,
  analytics: <><path d="M4 20V10M10 20V4M16 20v-7M22 20H2" /></>,
  studio: <><path d="m14 4 6 6-9.5 9.5H4.5v-6L14 4Z" /><path d="m12 6 6 6" /></>,
  chat: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4A2.5 2.5 0 0 1 4 13.5v-8Z" /></>,
  buyers: <><circle cx="12" cy="8" r="4" /><path d="M4.5 20.5c.8-4 3.8-6 7.5-6s6.7 2 7.5 6" /></>,
  deals: <><path d="M3 17 9 11l4 4 8-8" /><path d="M15 7h6v6" /></>,
  money: <><circle cx="12" cy="12" r="9" /><path d="M14.8 9.2c-.5-.9-1.5-1.4-2.8-1.4-1.6 0-2.8.8-2.8 2.1 0 3 5.8 1.3 5.8 4.3 0 1.3-1.3 2.2-3 2.2-1.4 0-2.5-.6-3-1.6M12 6v1.8M12 16.2V18" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  calendar: <><rect x="3.5" y="5" width="17" height="15.5" rx="2.5" /><path d="M3.5 10h17M8 3v4M16 3v4" /></>,
  bell: <><path d="M6 17V11a6 6 0 1 1 12 0v6l1.5 2h-15L6 17Z" /><path d="M10 21h4" /></>,
  refresh: <><path d="M20 11a8 8 0 0 0-14.5-4M4 4v4h4" /><path d="M4 13a8 8 0 0 0 14.5 4M20 20v-4h-4" /></>,
  arrow: <><path d="M5 12h14M13 6l6 6-6 6" /></>,
  plus: <><path d="M12 5v14M5 12h14" /></>,
  download: <><path d="M12 4v11M7 11l5 5 5-5M5 20h14" /></>,
  listing: <><path d="M4 20V9l8-5 8 5v11" /><path d="M9 20v-6h6v6" /></>,
  signout: <><path d="M14 4h4.5A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5H14M10 16l-4-4 4-4M6 12h10" /></>,
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, className = "h-[18px] w-[18px]" }: { name: IconName; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}

/** The brand mark: two overlapping circles, a pulse and its echo. */
export function Logo({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 40 40" className={className} aria-hidden="true">
      <circle cx="15" cy="20" r="11" fill="rgb(var(--accent))" />
      <circle cx="26" cy="20" r="11" fill="rgb(var(--accent))" opacity=".5" />
    </svg>
  );
}

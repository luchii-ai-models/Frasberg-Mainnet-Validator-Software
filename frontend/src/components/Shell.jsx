import { NavLink, Outlet } from "react-router-dom";
import { KeySquare, Activity, BellRing, Boxes, CalendarClock, FileSignature, FlaskConical, Hexagon, Hourglass, Inbox, KeyRound, Link2, Lock, Moon, Network, Rocket, Sun, Undo2 } from "lucide-react";
import { Toaster } from "@/components/ui/sonner";
import { Dot, ThemeProvider, useLoad, useTheme } from "@/components/kit";

const GROUPS = [
  [null, [["/", "Overview", Boxes], ["/fleet", "Validator Fleet", Network], ["/upgrades", "Upgrade Countdown", Hourglass]]],
  ["Operator", [
    ["/versions", "Required Versions", CalendarClock], ["/policies", "Signed Policies", FileSignature], ["/keys", "Signing Keys", KeyRound],
    ["/locks", "Locks & Fencing", Lock], ["/audit", "Audit Chain", Link2], ["/outbox", "Outbox", Inbox], ["/metrics", "Metrics & SLO", Activity],
    ["/alerts", "Alert Delivery", BellRing], ["/rollbacks", "Rollbacks", Undo2], ["/tests", "Test Runner", FlaskConical], ["/readiness", "Readiness Gate", Rocket], ["/access-keys", "API Keys", KeySquare]]],
];
const ALL = GROUPS.flatMap(([, items]) => items);
const tid = (label) => `nav-${label.toLowerCase().replace(/[^a-z]+/g, "-")}`;

const ThemeToggle = () => {
  const { theme, toggle } = useTheme();
  return (
    <button data-testid="theme-toggle" onClick={toggle} aria-label="Toggle theme"
      className="w-9 h-9 grid place-items-center rounded-lg border border-[var(--line)] bg-[var(--surface)] text-[var(--text-2)] hover:text-[var(--text)] hover:border-[var(--line-strong)] transition-colors">
      {theme === "light" ? <Moon className="w-4 h-4" /> : <Sun className="w-4 h-4" />}
    </button>
  );
};

const NavItem = ([to, label, Icon]) => (
  <NavLink key={to} to={to} end={to === "/"} data-testid={tid(label)}
    className={({ isActive }) => `flex items-center gap-3 mx-3 px-3 py-2 rounded-lg text-sm transition-colors duration-150 ${isActive ? "bg-[var(--brand-subtle)] text-[var(--brand)] font-semibold" : "text-[var(--text-2)] hover:text-[var(--text)] hover:bg-[var(--surface-hover)]"}`}>
    <Icon className="w-4 h-4" />{label}
  </NavLink>
);

function Sidebar({ ov }) {
  const v = ov?.validator;
  return (
    <aside className="w-64 shrink-0 border-r border-[var(--line)] bg-[var(--surface)] sticky top-0 h-screen hidden md:flex flex-col z-10">
      <div className="px-6 py-6 flex items-center gap-2.5">
        <span className="w-8 h-8 rounded-lg bg-[var(--brand)] grid place-items-center"><Hexagon className="w-4 h-4 text-white" /></span>
        <div>
          <div className="font-heading text-lg font-bold tracking-tight leading-none text-[var(--text)]">Frasberg</div>
          <div className="text-[11px] text-[var(--text-muted)] mt-1">Mainnet validator</div>
        </div>
      </div>
      <nav className="flex-1 overflow-y-auto scroll-thin pb-4 space-y-6">
        {GROUPS.map(([g, items]) => (
          <div key={g || "main"} data-testid={`nav-group-${(g || "main").toLowerCase()}`}>
            {g && <div className="px-6 mb-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">{g}</div>}
            <div className="space-y-0.5">{items.map(NavItem)}</div>
          </div>
        ))}
      </nav>
      <div className="m-3 p-3 rounded-xl bg-[var(--surface-2)] text-xs space-y-1" data-testid="sidebar-validator-identity">
        <div className="flex items-center gap-2 font-semibold text-[var(--text)]"><Dot tone={ov?.compliance?.healthy ? "ok" : "bad"} /><span className="font-mono">{v?.validatorId || "…"}</span></div>
        <div className="text-[var(--text-muted)]">v{v?.validatorVersion} · luchii {v?.luchiiVersion} · epoch {v?.epoch}</div>
        {ov?.settings?.safeMode && <div className="text-[var(--bad)] font-semibold">Safe mode</div>}
      </div>
    </aside>
  );
}

function Topbar({ ov }) {
  const healthy = ov?.compliance?.healthy;
  return (
    <header className="sticky top-0 z-20 bg-[var(--bg)]/85 backdrop-blur-md border-b border-[var(--line)]">
      <div className="px-4 sm:px-6 lg:px-10 h-14 flex items-center justify-between gap-3 max-w-[1440px] mx-auto">
        <div className="flex items-center gap-2 text-sm text-[var(--text-2)]" data-testid="topbar-network-status">
          <Dot tone={healthy ? "ok" : "bad"} /><span className="font-medium text-[var(--text)]">Frasberg Mainnet</span>
          <span className="hidden sm:inline text-[var(--text-muted)]">· {healthy ? "validator healthy" : "attention needed"}</span>
        </div>
        <ThemeToggle />
      </div>
      <div className="md:hidden flex gap-2 overflow-x-auto px-3 pb-3">
        {ALL.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"} data-testid={`m${tid(label)}`} className={({ isActive }) => `text-xs whitespace-nowrap px-3 py-1.5 rounded-full border ${isActive ? "border-[var(--brand)] text-[var(--brand)] bg-[var(--brand-subtle)]" : "border-[var(--line)] text-[var(--text-2)] hover:text-[var(--text)]"}`}>{label}</NavLink>
        ))}
      </div>
    </header>
  );
}

const Frame = ({ ov }) => {
  const { theme } = useTheme();
  return (
      <div className="min-h-screen bg-[var(--bg)] flex text-[var(--text)]">
        <Sidebar ov={ov} />
        <main className="flex-1 min-w-0">
          <Topbar ov={ov} />
          <div className="p-4 sm:p-6 lg:p-10 max-w-[1440px] mx-auto"><Outlet /></div>
          <Toaster position="bottom-right" theme={theme} />
        </main>
      </div>
  );
};

export default function Shell() {
  const [ov] = useLoad("/console/overview");
  return <ThemeProvider><Frame ov={ov} /></ThemeProvider>;
}

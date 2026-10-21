import { NavLink, Outlet } from "react-router-dom";
import { Activity, Boxes, CalendarClock, FileSignature, FlaskConical, GitBranch, Inbox, KeyRound, Link2, Lock, Rocket, Undo2 } from "lucide-react";
import { Dot, useLoad } from "@/components/kit";

const NAV = [
  ["/", "Overview", Boxes],
  ["/versions", "Required Versions", CalendarClock],
  ["/policies", "Signed Policies", FileSignature],
  ["/keys", "Signing Keys", KeyRound],
  ["/locks", "Locks & Fencing", Lock],
  ["/audit", "Audit Chain", Link2],
  ["/outbox", "Outbox", Inbox],
  ["/metrics", "Metrics & SLO", Activity],
  ["/rollbacks", "Rollbacks", Undo2],
  ["/tests", "Test Runner", FlaskConical],
  ["/readiness", "Readiness Gate", Rocket],
];

export default function Shell() {
  const [ov] = useLoad("/console/overview");
  const v = ov?.validator;
  return (
    <div className="min-h-screen bg-[#07090E] grid-bg noise flex">
      <aside className="w-64 shrink-0 border-r border-[#1E2638] bg-[#07090E]/95 backdrop-blur-xl sticky top-0 h-screen hidden md:flex flex-col z-10">
        <div className="px-5 py-6 border-b border-[#1E2638]">
          <div className="flex items-center gap-2">
            <GitBranch className="w-5 h-5 text-cyan-400" />
            <span className="font-heading text-xl font-extrabold tracking-tight">FRASBERG</span>
          </div>
          <div className="font-mono text-[10px] uppercase tracking-[0.25em] text-slate-500 mt-1">Mainnet Validator</div>
        </div>
        <nav className="flex-1 overflow-y-auto scroll-thin py-3">
          {NAV.map(([to, label, Icon]) => (
            <NavLink key={to} to={to} end={to === "/"} data-testid={`nav-${label.toLowerCase().replace(/[^a-z]+/g, "-")}`}
              className={({ isActive }) => `flex items-center gap-3 px-5 py-2.5 text-sm border-l-2 transition-colors duration-200 ${isActive ? "border-cyan-400 bg-[#0F131D] text-cyan-300" : "border-transparent text-slate-400 hover:text-slate-100 hover:bg-[#0F131D]/60"}`}>
              <Icon className="w-4 h-4" />{label}
            </NavLink>
          ))}
        </nav>
        <div className="p-5 border-t border-[#1E2638] font-mono text-[11px] space-y-1.5" data-testid="sidebar-validator-identity">
          <div className="flex items-center gap-2 text-slate-300"><Dot tone={ov?.compliance?.healthy ? "ok" : "bad"} />{v?.validatorId || "…"}</div>
          <div className="text-slate-500">v{v?.validatorVersion} · luchii {v?.luchiiVersion}</div>
          <div className="text-slate-500">epoch {v?.epoch} · {v?.channel}</div>
          {ov?.settings?.safeMode && <div className="text-[#FF3366]">SAFE MODE</div>}
        </div>
      </aside>
      <main className="flex-1 min-w-0 relative z-[2]">
        <div className="md:hidden flex gap-2 overflow-x-auto p-3 border-b border-[#1E2638]">
          {NAV.map(([to, label]) => <NavLink key={to} to={to} end={to === "/"} className="font-mono text-[11px] whitespace-nowrap px-2 py-1 border border-[#1E2638] rounded-sm text-slate-300">{label}</NavLink>)}
        </div>
        <div className="p-4 sm:p-6 lg:p-10 max-w-[1500px]"><Outlet /></div>
      </main>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";

export type ProfileId = "conservative" | "aggressive";
export type ViewTab = "dashboard" | "reports";

export type ProfileInfo = {
  profile_id: string;
  profile_name: string;
  status: string;
  starting_equity: number;
  current_equity: number;
  daily_pnl: number;
  port: number;
};

export type ProfileSidebarProps = {
  activeProfile: ProfileId;
  onProfileChange: (profile: ProfileId) => void;
  profiles: Record<ProfileId, ProfileInfo | null>;
  activeView: ViewTab;
  onViewChange: (view: ViewTab) => void;
};

export function ProfileSidebar({
  activeProfile,
  onProfileChange,
  profiles,
  activeView,
  onViewChange,
}: ProfileSidebarProps) {
  const conservativeInfo = profiles.conservative;
  const aggressiveInfo = profiles.aggressive;

  const formatCurrency = (value: number) => {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2,
    }).format(value ?? 0);
  };

  const formatPnl = (pnl: number) => {
    const sign = pnl >= 0 ? "+" : "";
    return `${sign}${formatCurrency(pnl)}`;
  };

  return (
    <aside className="profile-sidebar">
      {/* Brand */}
      <div className="sidebar-brand">
        <div className="brand-logo">BR</div>
        <div className="brand-text">
          <h1>Binance Bot</h1>
          <p>Research Trading</p>
        </div>
      </div>

      {/* View Navigation */}
      <nav className="view-nav">
        <button
          className={`view-nav-btn ${activeView === "dashboard" ? "active" : ""}`}
          onClick={() => onViewChange("dashboard")}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <rect x="3" y="3" width="7" height="7" rx="1" />
            <rect x="14" y="3" width="7" height="7" rx="1" />
            <rect x="3" y="14" width="7" height="7" rx="1" />
            <rect x="14" y="14" width="7" height="7" rx="1" />
          </svg>
          Dashboard
        </button>
        <button
          className={`view-nav-btn ${activeView === "reports" ? "active" : ""}`}
          onClick={() => onViewChange("reports")}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="16" y1="13" x2="8" y2="13" />
            <line x1="16" y1="17" x2="8" y2="17" />
            <polyline points="10 9 9 9 8 9" />
          </svg>
          Reports
        </button>
      </nav>

      {/* Profile Selection */}
      <nav className="profile-nav">
        <h3>Profiles</h3>
        
        <button
          className={`profile-card ${activeProfile === "conservative" ? "active" : ""} conservative`}
          onClick={() => onProfileChange("conservative")}
        >
          <div className="profile-card-header">
            <span className="profile-dot" data-status={conservativeInfo?.status === "running" ? "online" : "offline"} />
            <span className="profile-name">Conservative</span>
          </div>
          <div className="profile-card-stats">
            <div className="profile-stat">
              <span>Equity</span>
              <strong>{formatCurrency(conservativeInfo?.current_equity || 0)}</strong>
            </div>
            <div className="profile-stat">
              <span>PnL</span>
              <strong className={(conservativeInfo?.daily_pnl || 0) >= 0 ? "positive" : "negative"}>
                {formatPnl(conservativeInfo?.daily_pnl || 0)}
              </strong>
            </div>
          </div>
        </button>

        <button
          className={`profile-card ${activeProfile === "aggressive" ? "active" : ""} aggressive`}
          onClick={() => onProfileChange("aggressive")}
        >
          <div className="profile-card-header">
            <span className="profile-dot" data-status={aggressiveInfo?.status === "running" ? "online" : "offline"} />
            <span className="profile-name">Aggressive</span>
          </div>
          <div className="profile-card-stats">
            <div className="profile-stat">
              <span>Equity</span>
              <strong>{formatCurrency(aggressiveInfo?.current_equity || 0)}</strong>
            </div>
            <div className="profile-stat">
              <span>PnL</span>
              <strong className={(aggressiveInfo?.daily_pnl || 0) >= 0 ? "positive" : "negative"}>
                {formatPnl(aggressiveInfo?.daily_pnl || 0)}
              </strong>
            </div>
          </div>
        </button>
      </nav>

      {/* Status Footer */}
      <div className="sidebar-footer">
        <div className="status-row">
          <span className="status-dot" data-status={conservativeInfo?.status === "running" || aggressiveInfo?.status === "running" ? "online" : "offline"} />
          <span>Paper Trading</span>
        </div>
      </div>
    </aside>
  );
}

export function useProfiles() {
  const [profiles, setProfiles] = useState<Record<ProfileId, ProfileInfo | null>>({
    conservative: null,
    aggressive: null,
  });
  const [loading, setLoading] = useState(true);

  const fetchProfiles = useCallback(async () => {
    try {
      const response = await fetch("/api/profiles", { cache: "no-store" });
      if (response.ok) {
        const data = await response.json();
        setProfiles(data);
      }
    } catch (error) {
      console.error("Failed to fetch profiles:", error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProfiles();
    const interval = setInterval(fetchProfiles, 5000);
    return () => clearInterval(interval);
  }, [fetchProfiles]);

  return { profiles, loading, refetch: fetchProfiles };
}

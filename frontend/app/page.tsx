"use client";

import { useState, useCallback, useEffect } from "react";
import { TradingDashboard } from "@/components/trading-dashboard";

type ProfileId = "all" | "conservative" | "aggressive";

type ProfileInfo = {
  status: string;
  current_equity: number;
  daily_pnl: number;
};

type Trade = {
  symbol: string;
  direction: string;
  entry: number;
  exit: number;
  pnl: number;
  strategy_module: string;
  close_reason: string;
  timestamp: string;
  partial_exit?: boolean;
  regime?: string;
  r_multiple?: number;
  profile_id?: string;
};

export default function HomePage() {
  const [activeProfile, setActiveProfile] = useState<ProfileId>("all");
  const [profiles, setProfiles] = useState<Record<"conservative" | "aggressive", ProfileInfo | null>>({
    conservative: null,
    aggressive: null,
  });
  const [allTrades, setAllTrades] = useState<Trade[]>([]);

  const getApiBase = (profile: "conservative" | "aggressive") => {
    const envUrl =
      profile === "conservative"
        ? process.env.NEXT_PUBLIC_CONSERVATIVE_API_URL
        : process.env.NEXT_PUBLIC_AGGRESSIVE_API_URL;
    return (envUrl || "").replace(/\/+$/, "");
  };

  const getWsUrl = (profile: "conservative" | "aggressive") => {
    // Socket.IO accepts https base URLs and upgrades transport as needed.
    const apiBase = getApiBase(profile);
    return apiBase;
  };

  const fetchProfiles = useCallback(async () => {
    try {
      const conservativeApi = getApiBase("conservative");
      const aggressiveApi = getApiBase("aggressive");
      if (!conservativeApi || !aggressiveApi) {
        throw new Error("Missing NEXT_PUBLIC_CONSERVATIVE_API_URL or NEXT_PUBLIC_AGGRESSIVE_API_URL");
      }

      const [conservativeRes, aggressiveRes] = await Promise.all([
        fetch(`${conservativeApi}/api/state`).catch(() => null),
        fetch(`${aggressiveApi}/api/state`).catch(() => null),
      ]);

      const newProfiles: Record<"conservative" | "aggressive", ProfileInfo | null> = {
        conservative: null,
        aggressive: null,
      };

      if (conservativeRes?.ok) {
        const data = await conservativeRes.json();
        newProfiles.conservative = {
          status: data.status,
          current_equity: data.equity,
          daily_pnl: data.daily_pnl,
        };
      }

      if (aggressiveRes?.ok) {
        const data = await aggressiveRes.json();
        newProfiles.aggressive = {
          status: data.status,
          current_equity: data.equity,
          daily_pnl: data.daily_pnl,
        };
      }

      setProfiles(newProfiles);
    } catch (err) {
      console.error("Failed to fetch profiles:", err);
    }
  }, []);

  const fetchAllTrades = useCallback(async () => {
    try {
      const conservativeApi = getApiBase("conservative");
      const aggressiveApi = getApiBase("aggressive");
      if (!conservativeApi || !aggressiveApi) {
        throw new Error("Missing NEXT_PUBLIC_CONSERVATIVE_API_URL or NEXT_PUBLIC_AGGRESSIVE_API_URL");
      }

      const [conservativeTrades, aggressiveTrades] = await Promise.all([
        fetch(`${conservativeApi}/api/trades`)
          .then((res) => (res.ok ? res.json() : { trades: [] }))
          .catch(() => ({ trades: [] })),
        fetch(`${aggressiveApi}/api/trades`)
          .then((res) => (res.ok ? res.json() : { trades: [] }))
          .catch(() => ({ trades: [] })),
      ]);

      const allowedSymbols = new Set(["BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "BNBUSDT"]);
      
      const combinedTrades = [
        ...(conservativeTrades.trades || [])
          .filter((t: Trade) => allowedSymbols.has(t.symbol))
          .map((t: Trade) => ({ ...t, profile_id: "conservative" })),
        ...(aggressiveTrades.trades || [])
          .filter((t: Trade) => allowedSymbols.has(t.symbol))
          .map((t: Trade) => ({ ...t, profile_id: "aggressive" })),
      ].sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());

      setAllTrades(combinedTrades);
    } catch (err) {
      console.error("Failed to fetch trades:", err);
    }
  }, []);

  useEffect(() => {
    fetchProfiles();
    fetchAllTrades();
    const profileInterval = setInterval(fetchProfiles, 5000);
    const tradesInterval = setInterval(fetchAllTrades, 10000);
    return () => {
      clearInterval(profileInterval);
      clearInterval(tradesInterval);
    };
  }, [fetchProfiles, fetchAllTrades]);

  const profileOptions = [
    {
      id: "all",
      label: "All Profiles",
      status: profiles.conservative?.status === "running" || profiles.aggressive?.status === "running" ? "running" : "stopped",
      equity: (profiles.conservative?.current_equity || 0) + (profiles.aggressive?.current_equity || 0),
      pnl: (profiles.conservative?.daily_pnl || 0) + (profiles.aggressive?.daily_pnl || 0),
    },
    {
      id: "conservative",
      label: "Conservative",
      status: profiles.conservative?.status,
      equity: profiles.conservative?.current_equity || 0,
      pnl: profiles.conservative?.daily_pnl || 0,
    },
    {
      id: "aggressive",
      label: "Aggressive",
      status: profiles.aggressive?.status,
      equity: profiles.aggressive?.current_equity || 0,
      pnl: profiles.aggressive?.daily_pnl || 0,
    },
  ];

  // Determine API base - for "all" we default to conservative for general data
  const effectiveApiBase = activeProfile === "all" ? getApiBase("conservative") : getApiBase(activeProfile);
  const effectiveWsUrl = activeProfile === "all" ? getWsUrl("conservative") : getWsUrl(activeProfile);

  return (
    <TradingDashboard
      key={activeProfile}
      profileId={activeProfile}
      wsUrl={effectiveWsUrl}
      apiBase={effectiveApiBase}
      profileOptions={profileOptions}
      onProfileChange={(id) => setActiveProfile(id as ProfileId)}
      allProfilesTrades={allTrades}
    />
  );
}

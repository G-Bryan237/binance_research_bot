import { fetchReportsList, type ProfileId } from "@/lib/bot-api";
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const VALID_PROFILES = ["conservative", "aggressive"];

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ profile: string }> }
) {
  const { profile } = await params;
  
  if (!VALID_PROFILES.includes(profile)) {
    return Response.json({ error: "Invalid profile" }, { status: 400 });
  }

  const reports = await fetchReportsList(profile as ProfileId);
  if (!reports) {
    return Response.json({ error: "Failed to fetch reports" }, { status: 502 });
  }

  return Response.json(reports);
}

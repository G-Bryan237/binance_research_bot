import { proxyToProfileBackend, type ProfileId } from "@/lib/bot-api";
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

  return proxyToProfileBackend(profile as ProfileId, "/api/signals");
}
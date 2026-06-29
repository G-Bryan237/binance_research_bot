import { proxyToProfileBackend, postToProfileBackend, type ProfileId } from "@/lib/bot-api";
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

  return proxyToProfileBackend(profile as ProfileId, "/api/balance");
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ profile: string }> }
) {
  const { profile } = await params;
  
  if (!VALID_PROFILES.includes(profile)) {
    return Response.json({ error: "Invalid profile" }, { status: 400 });
  }

  const body = await request.json();
  return postToProfileBackend(profile as ProfileId, "/api/balance", body);
}

export async function OPTIONS(): Promise<Response> {
  return new Response(null, {
    status: 204,
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    },
  });
}

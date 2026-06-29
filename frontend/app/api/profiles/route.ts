import { fetchAllProfiles } from "@/lib/bot-api";
import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET() {
  const profiles = await fetchAllProfiles();
  return NextResponse.json(profiles);
}

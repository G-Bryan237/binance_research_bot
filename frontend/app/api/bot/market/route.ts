import type { NextRequest } from "next/server";

import { proxyToBackend } from "@/lib/bot-api";

export async function GET(request: NextRequest): Promise<Response> {
  const query = request.nextUrl.searchParams.toString();
  const suffix = query ? `?${query}` : "";
  return proxyToBackend(`/api/market${suffix}`);
}

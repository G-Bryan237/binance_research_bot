import { proxyToBackend } from "@/lib/bot-api";

export async function GET(): Promise<Response> {
  return proxyToBackend("/api/health");
}

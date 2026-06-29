import { NextRequest } from "next/server";

const BACKEND_URL = process.env.PYTHON_BACKEND_BASE_URL || "http://127.0.0.1:5000";

export async function GET(): Promise<Response> {
  try {
    const upstream = await fetch(`${BACKEND_URL}/api/balance`, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });

    const body = await upstream.text();
    const headers = new Headers();
    headers.set("content-type", "application/json; charset=utf-8");
    headers.set("cache-control", "no-store");

    return new Response(body, { status: upstream.status, headers });
  } catch (error) {
    return Response.json(
      { error: "Failed to connect to backend" },
      { status: 503 }
    );
  }
}

export async function POST(request: NextRequest): Promise<Response> {
  try {
    const body = await request.json();
    
    const upstream = await fetch(`${BACKEND_URL}/api/balance`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(body),
    });

    const responseBody = await upstream.text();
    const headers = new Headers();
    headers.set("content-type", "application/json; charset=utf-8");
    headers.set("cache-control", "no-store");

    return new Response(responseBody, { status: upstream.status, headers });
  } catch (error) {
    return Response.json(
      { error: "Failed to connect to backend" },
      { status: 503 }
    );
  }
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

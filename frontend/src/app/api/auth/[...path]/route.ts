import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

/**
 * Proxy auth requests to the FastAPI backend.
 * Forwards cookies in both directions so httpOnly session cookies work.
 */
export async function POST(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const backendPath = `/auth/${path.join("/")}`;
  const body = await req.text();

  const res = await fetch(`${BACKEND_URL}${backendPath}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Cookie: req.headers.get("cookie") || "",
    },
    body,
  });

  const data = await res.json();
  const response = NextResponse.json(data, { status: res.status });

  // Forward Set-Cookie headers from backend
  const setCookie = res.headers.getSetCookie();
  for (const cookie of setCookie) {
    response.headers.append("Set-Cookie", cookie);
  }

  return response;
}

export async function GET(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const backendPath = `/auth/${path.join("/")}`;

  const res = await fetch(`${BACKEND_URL}${backendPath}`, {
    headers: {
      Cookie: req.headers.get("cookie") || "",
    },
  });

  const data = await res.json();
  const response = NextResponse.json(data, { status: res.status });

  const setCookie = res.headers.getSetCookie();
  for (const cookie of setCookie) {
    response.headers.append("Set-Cookie", cookie);
  }

  return response;
}

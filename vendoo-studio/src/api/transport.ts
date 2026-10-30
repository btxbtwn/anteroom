/** The existing API client uses this for desktop HTTP and authenticated cloud HTTP. */
export interface HostedApiSession {
  origin: string;
  accessToken: () => Promise<string | null>;
  unauthorized: () => void;
}
let hostedSession: HostedApiSession | null = null;

export function installHostedApiSession(session: HostedApiSession): () => void {
  hostedSession = session;
  return () => { if (hostedSession === session) hostedSession = null; };
}

export function hasHostedApiSession() { return hostedSession !== null; }

export async function studioFetch(input: string, options: RequestInit = {}): Promise<Response> {
  const session = hostedSession;
  if (!session) return fetch(input, options);
  const url = new URL(input, session.origin);
  if (url.origin !== session.origin || !url.pathname.startsWith("/api/")) {
    throw new Error("Anteroom API requests must stay on this app's origin.");
  }
  const token = await session.accessToken();
  if (!token || hostedSession !== session) throw new Error("Sign in to continue.");
  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(url.href, { ...options, headers, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer" });
  if (response.status === 401 && hostedSession === session) session.unauthorized();
  return response;
}

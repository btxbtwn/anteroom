/** Where Supabase's OAuth server sends the seller to approve an app (its Authorization Path). */
export const CONSENT_PATH = "/oauth/consent";

export function isConsentPage(pathname: string): boolean {
  return pathname === CONSENT_PATH;
}

/** The pending request Supabase names in the link, or null. */
export function consentRequest(search: string): string | null {
  const id = new URLSearchParams(search).get("authorization_id")?.trim();
  return id ? id : null;
}

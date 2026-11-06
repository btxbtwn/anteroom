/** Where the landing page sends a visitor who wants to sign in or create an account. */
export const SIGN_IN_PATH = "/?sign-in";
export const CREATE_ACCOUNT_PATH = "/?create-account";

// Campaign tags a shared link may carry; they still show the landing page.
const isCampaignTag = (name: string) => name.startsWith("utm_") || name === "ref";

/**
 * Signed-out visitors to the bare root in a browser see the landing page. Any other
 * query (sign-in, a confirmation or reset link, an error, a link to a listing) and
 * the Mac app go straight to sign-in.
 */
export function showsLanding(search: string, mac: boolean): boolean {
  if (mac) return false;
  return [...new URLSearchParams(search).keys()].every(isCampaignTag);
}

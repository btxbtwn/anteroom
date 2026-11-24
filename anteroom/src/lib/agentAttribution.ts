/**
 * Who started a marketplace action, when it was not the seller in Anteroom.
 * The server records an AI agent's access token or connected app as
 * ``requested_by = "mcp:<name>"`` on the job it starts.
 */
export function fromAgent(requestedBy: string | null | undefined): string | null {
  if (!requestedBy?.startsWith("mcp:")) return null;
  const name = requestedBy.slice(4).trim();
  return name ? `From ${name} (AI assistant)` : "From your AI assistant";
}

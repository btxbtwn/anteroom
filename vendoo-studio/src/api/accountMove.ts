import { errorMessage } from "./client";
import { studioFetch } from "./transport";

export interface AccountMoveResult {
  records: number;
  photos: number;
  missing_photos: number;
  /** Photos the account could not take, such as unreadable files. */
  refused_photos: number;
  settings: number;
  keys: string[];
  paused_automations: number;
}

export interface AccountMoveStatus {
  hosted_url: string;
  /** Set once this Mac has moved into an account. */
  moved: (AccountMoveResult & { user_id: string; email: string | null; moved_at: string }) | null;
  listings: number;
  photos: number;
  progress: {
    state: "idle" | "running" | "done" | "failed";
    step?: string;
    done?: number;
    total?: number;
    error?: string;
    result?: AccountMoveResult;
  };
}

async function read(response: Response, fallback: string): Promise<AccountMoveStatus> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(errorMessage(body, fallback));
  }
  return response.json();
}

export async function getAccountMove(): Promise<AccountMoveStatus> {
  return read(await studioFetch("/api/account-move"), "Could not read this Mac’s listings.");
}

export async function startAccountMove(email: string, password: string): Promise<AccountMoveStatus> {
  return read(await studioFetch("/api/account-move", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  }), "Could not start the move.");
}

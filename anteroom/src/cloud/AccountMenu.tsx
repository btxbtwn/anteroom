import { useState } from "react";
import { UserRound } from "lucide-react";
import { Popover, PopoverPopup, PopoverTrigger } from "../components/ui/popover";
import { api } from "../api/client";
import { confirmDialog } from "../ui/confirmDialog";
import { ChromeConnection } from "./ChromeConnection";
import { ACCOUNT_DELETION_MESSAGE } from "./accountDeletion";
import type { BrowserAccount } from "../api/cloudExtension";

/** The profile button in the top bar, with the extension connection, Sign out and Delete account. */
export function AccountMenu({ account, chrome, canDelete, onSignOut }: { account: BrowserAccount; chrome: boolean; canDelete: boolean; onSignOut: () => void }) {
  const initial = account.email.trim().charAt(0).toUpperCase();
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  async function deleteAccount() {
    if (!await confirmDialog(ACCOUNT_DELETION_MESSAGE, { variant: "destructive", confirmLabel: "Delete account" })) return;
    setDeleting(true);
    setError("");
    try {
      await api.account.delete();
      // The account is gone; only this browser's copy of the session is left to clear.
      onSignOut();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not delete your account. Try again.");
      setDeleting(false);
    }
  }
  return <Popover>
    <PopoverTrigger className="cloud-profile" aria-label="Account" title={account.email || "Account"}>
      {initial || <UserRound size={16} aria-hidden/>}
    </PopoverTrigger>
    {/* Kept mounted while closed: the connection keeps its status and relays marketplace commands. */}
    <PopoverPopup keepMounted align="end" width="md" padding="compact" className="cloud-profile-menu">
      {account.email && <p className="cloud-profile-email">{account.email}</p>}
      {chrome && <ChromeConnection account={account}/>}
      <button type="button" className="cloud-profile-signout" onClick={onSignOut} disabled={deleting}>Sign out</button>
      {canDelete && <button type="button" className="cloud-profile-delete" onClick={() => void deleteAccount()} disabled={deleting}>
        {deleting ? "Deleting account…" : "Delete account…"}
      </button>}
      {error && <p className="cloud-profile-error" role="alert">{error}</p>}
    </PopoverPopup>
  </Popover>;
}

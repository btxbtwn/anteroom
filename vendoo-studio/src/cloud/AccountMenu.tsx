import { UserRound } from "lucide-react";
import { Popover, PopoverPopup, PopoverTrigger } from "../components/ui/popover";
import { ChromeConnection } from "./ChromeConnection";
import type { BrowserAccount } from "../api/cloudExtension";

/** The profile button in the top bar, with the extension connection and Sign out. */
export function AccountMenu({ account, chrome, onSignOut }: { account: BrowserAccount; chrome: boolean; onSignOut: () => void }) {
  const initial = account.email.trim().charAt(0).toUpperCase();
  return <Popover>
    <PopoverTrigger className="cloud-profile" aria-label="Account" title={account.email || "Account"}>
      {initial || <UserRound size={16} aria-hidden/>}
    </PopoverTrigger>
    {/* Kept mounted while closed: the connection keeps its status and relays marketplace commands. */}
    <PopoverPopup keepMounted align="end" width="md" padding="compact" className="cloud-profile-menu">
      {account.email && <p className="cloud-profile-email">{account.email}</p>}
      {chrome && <ChromeConnection account={account}/>}
      <button type="button" className="cloud-profile-signout" onClick={onSignOut}>Sign out</button>
    </PopoverPopup>
  </Popover>;
}

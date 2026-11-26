import { EllipsisIcon } from "lucide-react";
import { useClearListing } from "./ClearListingButton";
import { Button } from "./ui/button";
import { Menu, MenuItem, MenuPopup, MenuTrigger } from "./ui/menu";

/** Everything rare enough not to earn a place in the header. Clearing is the
    only item today, and it stays out of reach of the tabs next door. */
export function ListingActionsMenu({ convId, onCleared }: { convId: string; onCleared?: () => void }) {
  const { clear, isPending } = useClearListing(convId, onCleared);

  return (
    <Menu>
      <MenuTrigger
        render={<Button variant="ghost" size="icon-xs" aria-label="More listing actions" />}
      >
        <EllipsisIcon />
      </MenuTrigger>
      <MenuPopup align="end" width="content">
        <MenuItem variant="destructive" disabled={isPending} onClick={() => void clear()}>
          {isPending ? "Clearing…" : "Clear listing"}
        </MenuItem>
      </MenuPopup>
    </Menu>
  );
}

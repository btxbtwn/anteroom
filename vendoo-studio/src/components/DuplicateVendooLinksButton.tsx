import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLinkIcon } from "lucide-react";
import { api } from "../api/client";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogClose,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogPanel,
  DialogPopup,
  DialogTitle,
  DialogTrigger,
} from "./ui/dialog";
import { Spinner } from "./ui/spinner";

export function DuplicateVendooLinksButton({ onSelect }: { onSelect: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const { data, isFetching, error } = useQuery({
    queryKey: ["duplicate-vendoo-links"],
    queryFn: api.conversations.duplicateVendooLinks,
    enabled: open,
    staleTime: 0,
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button size="xs" variant="outline" aria-label="Check duplicate Vendoo links" />}>
        Check duplicates
      </DialogTrigger>
      <DialogPopup className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Duplicate Vendoo links</DialogTitle>
          <DialogDescription>
            Anteroom listings that share the same Vendoo item. Open a listing to review its link.
          </DialogDescription>
        </DialogHeader>
        <DialogPanel aria-live="polite">
          {isFetching ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Spinner size="sm" tone="muted" />
              Checking saved links…
            </p>
          ) : error ? (
            <p role="alert" className="text-sm text-destructive-foreground">{error.message}</p>
          ) : !data?.length ? (
            <p className="text-sm text-muted-foreground">No duplicate Vendoo links found.</p>
          ) : (
            data.map((group) => (
              <section key={group.vendoo_item_id} className="space-y-2">
                <a
                  href={group.vendoo_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-sm font-medium text-foreground hover:underline"
                >
                  Vendoo item {group.vendoo_item_id}
                  <ExternalLinkIcon className="size-3.5 text-muted-foreground" />
                </a>
                <div className="flex flex-wrap gap-2">
                  {group.listings.map((listing) => (
                    <Button
                      key={listing.id}
                      size="xs"
                      variant="outline"
                      onClick={() => {
                        setOpen(false);
                        onSelect(listing.id);
                      }}
                    >
                      {listing.title}
                    </Button>
                  ))}
                </div>
              </section>
            ))
          )}
        </DialogPanel>
        <DialogFooter>
          <DialogClose render={<Button />}>Done</DialogClose>
        </DialogFooter>
      </DialogPopup>
    </Dialog>
  );
}

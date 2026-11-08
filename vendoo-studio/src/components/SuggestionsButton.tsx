import { useState } from "react";
import { LightbulbIcon } from "lucide-react";
import { SuggestionsPanel } from "./SuggestionsPanel";
import { Button } from "./ui/button";
import { Dialog, DialogTrigger, DialogPopup, DialogTitle, DialogDescription, DialogHeader, DialogPanel } from "./ui/dialog";

export function SuggestionsButton({ onSelect }: { onSelect: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="ghost" size="sm" className="w-full justify-start" />}>
        <LightbulbIcon /> Suggestions
      </DialogTrigger>
      <DialogPopup>
        <DialogHeader>
          <DialogTitle>Suggestions</DialogTitle>
          <DialogDescription>Listings to review or refresh, ranked by age, recent sales and season.</DialogDescription>
        </DialogHeader>
        <DialogPanel>
          {open && <SuggestionsPanel onSelect={(id) => { setOpen(false); onSelect(id); }} />}
        </DialogPanel>
      </DialogPopup>
    </Dialog>
  );
}

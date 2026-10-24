import type { ReactNode } from "react";
import { Tabs } from "@base-ui/react/tabs";

export function WorkspaceTabs({ label, items, value, onChange, children, main = false }: {
  label: string; items: readonly string[]; value: string; onChange: (value: string) => void; children: ReactNode; main?: boolean;
}) {
  return <Tabs.Root value={value} onValueChange={(next) => onChange(String(next))} className="ebay-tabs">
    <Tabs.List aria-label={label} className={main ? "ebay-navigation" : "ebay-task-navigation"}>
      {items.map((item) => <Tabs.Tab key={item} value={item}>{item}</Tabs.Tab>)}
    </Tabs.List>
    <Tabs.Panel value={value} className="ebay-tab-content">{children}</Tabs.Panel>
  </Tabs.Root>;
}

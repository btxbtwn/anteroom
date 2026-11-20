import type { ReactNode } from "react";
import { Tabs } from "@base-ui/react/tabs";

export function WorkspaceTabs({ label, items, value, onChange, children, main = false, disabled = [] }: {
  label: string; items: readonly string[]; value: string; onChange: (value: string) => void; children: ReactNode;
  main?: boolean; disabled?: readonly string[];
}) {
  return <Tabs.Root value={value} onValueChange={(next) => onChange(String(next))} className="ebay-tabs">
    <Tabs.List aria-label={label} className={main ? "ebay-navigation" : "ebay-task-navigation"}>
      {items.map((item) => <Tabs.Tab key={item} value={item} disabled={disabled.includes(item)}>{item}</Tabs.Tab>)}
    </Tabs.List>
    <Tabs.Panel value={value} className="ebay-tab-content">{children}</Tabs.Panel>
  </Tabs.Root>;
}

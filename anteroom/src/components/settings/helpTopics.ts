/** Task-based how-tos for Settings → Help. Steps name the controls exactly as the UI shows them. */

export type HelpTopic = {
  id: string;
  title: string;
  steps: readonly string[];
  /** Shown only in the local desktop app (not hosted web). */
  desktopOnly?: boolean;
  /** Alternative steps shown in the hosted web app when the workflow differs. */
  hostedSteps?: readonly string[];
};

export const HELP_TOPICS: readonly HelpTopic[] = [
  {
    id: "help-create-listing",
    title: "Create a listing from photos",
    steps: [
      "Drop photos anywhere in the window, or press **+** (New listing) beside the listings search and use **Add Photos** on the **General** tab.",
      "On **General**, fill in **Measurements (in)** and **Notes & Labels** with what you know about the item.",
      "Open **Chat** and press **Generate listing**.",
      "Check the result on **General** and each marketplace tab. Ask in the chat for any change you want.",
    ],
  },
  {
    id: "help-bulk-upload",
    title: "Upload a whole box at once",
    steps: [
      "Put each item's photos in its own folder. Drop all the folders on the window together, or press **Add Folder** and pick the folder that holds them.",
      "In **Set details for … listings**, set what applies to every item: the **Box** it came from, the **Cost of goods (COG)** and **Labels** such as a bin.",
      "For each item, type the **Size** from its tag and the **SKU**, pick **Top**, **Pants** or **Skirt**, and type the measurements in inches.",
      "Press **Read size tags** to fill blank sizes from the size tag in the photos. A size you typed stays. When a tag is missing, unreadable or shows two sizes, the item says so and the size stays blank for you to type.",
      "Press **Read measurements** to fill blank measurements from the yardstick in the photos. Filled boxes are outlined, so check them.",
      "Press **Create and generate** to write every listing, or **Create drafts only** to write them later.",
    ],
  },
  {
    id: "help-regenerate",
    title: "Regenerate or rewrite listings",
    steps: [
      "One listing: press **Regenerate** in the listing header. If it has a price, pick the new price and press **Confirm**; if not, press **Rewrite**.",
      "Several listings: press **Select** above the sidebar list, tick the listings (each needs photos), then choose **More** → **Regenerate…** in the bar at the bottom.",
      "If any have a price, choose **Keep prices**, a drop for each, or **Each listing's suggestion**.",
      "In **Review … listings before rewriting**, check the photos, size, SKU, COG, labels and measurements (or press **Read size tags** and **Read measurements**), then press **Rewrite**.",
      "The new copy stays in Anteroom until you update it on the marketplace. To go back, press **History** at the bottom of the listing editor, then **Restore this version** and **Confirm restore**.",
    ],
  },
  {
    id: "help-price-drops",
    title: "Drop prices on stale listings",
    steps: [
      "Find what is sitting: open **Filters** in the sidebar and choose a **Stale since listed** age, or sort by **Stalest**. **Suggestions** also lists listings up past your stale threshold (60 days unless you change it in Settings → Listings → Inventory) as **Refresh or consider price drop**.",
      "One listing: press **Regenerate**. The dialog suggests a new price from live comps and your past drops. Pick one and press **Confirm**.",
      "Many at once: press **Select**, tick them, choose **More** → **Regenerate…**, choose −10%, −15% or −20% each, or **Each listing's suggestion**, then **Drop and rewrite**.",
      "Open each listing and update it on the marketplace to carry the new price live.",
      "For an eBay sale plan, open **Marketplaces** → **eBay** and find **Sale calendar**. Connected accounts show it under **Marketing**. Choose **Plan a sale**.",
    ],
  },
  {
    id: "help-boxes",
    title: "Record boxes you bought",
    steps: [
      "Open **Analytics** in the sidebar and switch to **Boxes**.",
      "Press **Add a box**, fill in the store, box, price, shipping and pieces, and press **Add box**.",
      "When you upload that box's items, pick it in **Box** so each piece carries its share of the cost.",
      "Record any unsellable pieces on the box so their cost counts toward the pieces that sell.",
    ],
  },
  {
    id: "help-analytics",
    title: "Read Analytics",
    steps: [
      "Open **Analytics** in the sidebar. It starts on **Sales records**, where **Record amounts** lets you fill in a sale's totals.",
      "Switch to **Inventory analytics** for inventory metrics and breakdowns. Pick a range at the top, from **This week** to **All time**, or **Custom** with your own From and To dates.",
      "The top row shows **Revenue**, **Profit**, **Items sold**, **Median days to sell**, **Sell-through** and **Profit margin**. The chart switches between **Revenue**, **Profit** and **Items sold**.",
      "**Marketplaces**, **Categories**, **Brands** and **Labels** rank what sells. **Inventory today** and **Review your oldest listings** show what is still sitting.",
      "Missing a recent sale? Open the listing and record it, or wait for sale detection on a connected marketplace.",
    ],
  },
  {
    id: "help-connect-agent",
    title: "Connect an agent",
    desktopOnly: true,
    steps: [
      "Go to Settings → Agents. It shows whether the **anteroom** command is installed and the address an agent on this Mac should use.",
      "If it says the command is not installed, run the install command shown there from the anteroom folder, then reopen Agents.",
      "Press **Copy** on the login command and run it in a terminal. On this Mac the command already talks to this app, so this saves that address.",
      "Pick **Cursor**, **Claude Desktop**, or **Claude Code**, press **Copy**, and paste that setup into the place the row names.",
      "Press **Test connection**. It only checks that the command can reach this app. An agent still cannot publish or delete unless you confirm it.",
    ],
  },
  {
    id: "help-connect-chrome",
    title: "Connect Chrome and the extension",
    steps: [
      "Go to Settings → Connections and press **Connect Chrome**. Your everyday Chrome opens.",
      "First time only: copy the **Listing extension folder** path, open chrome://extensions, turn on Developer mode, click Load unpacked, press ⌘⇧G, paste the path, then Go and Open.",
      "Sign in to the marketplaces you list on in that Chrome window.",
      "Settings → Connections shows **Connected** when it works. If it says offline or outdated, press **Connect Chrome** again.",
    ],
    hostedSteps: [
      "Install **Anteroom Bridge** in desktop Chrome from the [Chrome Web Store](https://chromewebstore.google.com/detail/empinkdlhndpfoigoipbiiofaeepcheo), then reload this page.",
      "Open the **Account** menu in the top-right corner and choose **Connect Anteroom Extension**, then approve the prompt in Chrome.",
      "Sign in to Poshmark, Mercari, or Depop in that same Chrome window. (eBay and Etsy connect directly in Settings → Connections without Chrome.)",
      "Keep an Anteroom tab open in Chrome whenever you want marketplace actions or automatic sale checks to run.",
    ],
  },
  {
    id: "help-backups",
    title: "Back up and restore",
    desktopOnly: true,
    steps: [
      "Anteroom backs itself up when it starts, every six hours, and before any update. To make one now, go to Settings → Data and press **Back up now**.",
      "Set a **Backup folder** that is not on this Mac, such as an external drive or a synced folder, and press **Save**. Every backup and photo is copied there.",
      "To undo changes on one listing, press **History** at the bottom of its editor, then **Restore this version** and **Confirm restore**. This changes Anteroom only.",
    ],
  },
];

/** Help topics appropriate for the current runtime (desktop or hosted web). */
export function helpTopicsFor(hosted: boolean): readonly HelpTopic[] {
  if (!hosted) return HELP_TOPICS;
  return HELP_TOPICS.filter((topic) => !topic.desktopOnly).map((topic) =>
    topic.hostedSteps ? { ...topic, steps: topic.hostedSteps } : topic,
  );
}

/** A topic's steps as a numbered markdown list. */
export function helpTopicMarkdown(topic: HelpTopic): string {
  return topic.steps.map((step, index) => `${index + 1}. ${step}`).join("\n");
}

/** Task-based how-tos for Settings → Help. Steps name the controls exactly as the UI shows them. */

export type HelpTopic = {
  id: string;
  title: string;
  steps: readonly string[];
};

export const HELP_TOPICS: readonly HelpTopic[] = [
  {
    id: "help-create-listing",
    title: "Create a listing from photos",
    steps: [
      "Drop photos anywhere in the window, or press **+** (New listing) at the end of the listing tabs and use **Add Photos** on the **Input** tab.",
      "Type the measurements and anything else you know into Item details on **Input**.",
      "Press **Generate Listing** in the chat.",
      "Check the result on the **Forms** tab. Ask in the chat for any change you want.",
    ],
  },
  {
    id: "help-bulk-upload",
    title: "Upload a whole box at once",
    steps: [
      "Put each item's photos in its own folder. Drop all the folders on the window together, or press **Add Folder** and pick the folder that holds them.",
      "In **Set details for … listings**, set what applies to every item: the **Box** it came from, the **Cost of goods (COG)** and **Labels** such as a bin.",
      "For each item, type the **SKU**, pick **Top**, **Pants** or **Skirt**, and type the measurements in inches.",
      "Press **Read from photos** to fill blank measurements from the yardstick in the photos. Filled boxes are outlined, so check them.",
      "Press **Create and generate** to write every listing, or **Create drafts only** to write them later.",
    ],
  },
  {
    id: "help-send-to-vendoo",
    title: "Send to Vendoo and update a draft",
    steps: [
      "Connect Chrome and sign in to Vendoo first (see **Connect Chrome and the extension** below).",
      "Open the listing and press **Send to Vendoo**. Anteroom creates a Vendoo draft and links it to the listing.",
      "After you change a listing, its header shows **Unsent edits**. Press **Update Vendoo** to write the changes onto the same draft; photos already in Vendoo stay. **Discard** puts the listing back to what Vendoo has.",
      "Sends run one at a time. Open **Queue** in the sidebar to see what is waiting or to **Cancel** one.",
      "Anteroom never publishes, lists or delists anything on its own: it only saves drafts. When you are ready, press **Open listing** and list the item yourself in Vendoo. If a live listing needs relisting to show your updates, follow the steps in its **Relist to show your updates** box, then press **I've relisted**.",
    ],
  },
  {
    id: "help-regenerate",
    title: "Regenerate or rewrite listings",
    steps: [
      "One listing: press **Regenerate** in the listing header. If it has a price, pick the new price and press **Confirm**; if not, press **Rewrite**.",
      "Several listings: press **Select** above the sidebar list, tick the listings (each needs photos), then press **Regenerate** in the bar at the bottom.",
      "If any have a price, choose **Keep prices**, a drop for each, or **Each listing's suggestion**.",
      "In **Review … listings before rewriting**, check the photos, SKU, COG, labels and measurements, then press **Rewrite**.",
      "The new copy stays in Anteroom until you press **Update Vendoo**. To go back, press **History** in the listing header and **Restore this version**.",
    ],
  },
  {
    id: "help-price-drops",
    title: "Drop prices on stale listings",
    steps: [
      "Find what is sitting: open **Filters** in the sidebar and choose a **Stale since listed** age, or sort by **Stalest**. **Suggestions** also lists listings up for 60 days or more as **Refresh or consider price drop**.",
      "One listing: press **Regenerate**. The dialog suggests a new price from live comps and your past drops. Pick one and press **Confirm**.",
      "Many at once: press **Select**, tick them, press **Regenerate**, choose −10%, −15% or −20% each, or **Each listing's suggestion**, then **Drop and rewrite**.",
      "Press **Update Vendoo** on each listing to carry the new price to its draft.",
      "For a timed sale instead, open **Marketing** and use **Plan a sale** in the Sale calendar.",
    ],
  },
  {
    id: "help-sourcing",
    title: "Find boxes to buy and record what you bought",
    steps: [
      "Open **Sourcing** in the sidebar and stay on **Wholesale boxes**.",
      "Set your **Budget**, **Ship to** ZIP and **Minimum return**. **More options** holds your selling pace, fees and cost per sold piece; they follow your own sales unless you type a number.",
      "Anteroom lists the boxes worth buying under **Choose a buy list**. Open the store's cart from there and pay on the store's site; Anteroom never buys for you.",
      "Once you have paid, press **I bought this** on that box. For a box bought elsewhere, press **Add a box** under **Boxes you bought**, fill in the store, box, price, shipping and pieces, and press **Add box**.",
      "When you upload that box's items, pick it in **Box** so each piece carries its share of the cost.",
      "To check a single item before you buy it, use the **Scout an item** tab.",
    ],
  },
  {
    id: "help-analytics",
    title: "Read Analytics",
    steps: [
      "Open **Analytics** in the sidebar.",
      "Pick a range at the top, from **This week** to **All time**, or **Custom** with your own From and To dates.",
      "The top row shows **Revenue**, **Sold**, **Sell-through rate**, **Profit**, **Profit margin** and **Median time to sell**. The chart switches between revenue, profit and sales.",
      "**Marketplaces**, **Categories** and **Brands** rank what sells. **Inventory today** and **Review your oldest listings** show what is still sitting.",
      "Missing a recent sale? Press **Resync Vendoo data**.",
    ],
  },
  {
    id: "help-connect-chrome",
    title: "Connect Chrome and the extension",
    steps: [
      "Go to Settings → Connections and press **Connect Chrome**. Vendoo opens in your everyday Chrome.",
      "First time only: copy the **Listing extension folder** path, open chrome://extensions, turn on Developer mode, click Load unpacked, press ⌘⇧G, paste the path, then Go and Open.",
      "Sign in to Vendoo in that Chrome window.",
      "Settings → Connections shows **Connected** when it works. If it says offline or outdated, press **Connect Chrome** again.",
    ],
  },
  {
    id: "help-backups",
    title: "Back up and restore",
    steps: [
      "Anteroom backs itself up when it starts, every six hours, and before any update. To make one now, go to Settings → Data and press **Back up now**.",
      "Set a **Backup folder** that is not on this Mac, such as an external drive or a synced folder, and press **Save**. Every backup and photo is copied there.",
      "To undo changes on one listing, press **History** in its header, then **Restore this version** and **Confirm restore**. This changes Anteroom only, not Vendoo.",
    ],
  },
];

/** A topic's steps as a numbered markdown list. */
export function helpTopicMarkdown(topic: HelpTopic): string {
  return topic.steps.map((step, index) => `${index + 1}. ${step}`).join("\n");
}

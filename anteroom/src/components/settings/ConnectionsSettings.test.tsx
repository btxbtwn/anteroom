import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectionsSettings } from "./ConnectionsSettings";
import { desktopRuntime, WorkspaceRuntimeProvider } from "../../app/runtime";

const { useQuery, useMutation } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({
  useQuery,
  useMutation,
  useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn() }),
}));

beforeEach(() => {
  useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false, error: null });
  useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) => {
    if (queryKey[0] === "extension-status") return { data: { connected: true } };
    if (queryKey[0] === "marketplace-sessions") {
      return { isSuccess: true, data: { sessions: {
        depop: { state: "signed_in", expires_at: null, label: "ada", expiring: false },
        poshmark: { state: "signed_out", expires_at: null, label: null, expiring: false },
        mercari: { state: "expired", expires_at: 10, label: null, expiring: false },
      } } };
    }
    if (queryKey[0] === "direct-connections") return { data: { accounts: {} } };
    if (queryKey[0] === "settings-tailscale") return { isPending: true };
    return { data: undefined };
  });
});

describe("Connections settings", () => {
  it("keeps cached sign-in unconfirmed while a retry is pending", () => {
    useMutation.mockReturnValue({ mutate: vi.fn(), isPending: true, error: null });
    const html = renderToStaticMarkup(<ConnectionsSettings />);
    expect(html).toContain("Sign-in not confirmed");
    expect(html).not.toContain("Signed in as ada");
  });
  it("identifies a sale-check timeout separately from a saved sign-in", () => {
    const query = useQuery.getMockImplementation()!;
    useQuery.mockImplementation((options) => options.queryKey[0] === "sale-detection"
      ? { data: { checks: { depop: "Chrome did not answer in time. Try again." } } }
      : query(options));
    const html = renderToStaticMarkup(<ConnectionsSettings />);
    expect(html).toContain("Sale check failed: Chrome did not answer in time. Try again.");
    expect(html).toContain("Signed in as ada");
  });
  it("does not show a cached green sign-in after reconnect times out", () => {
    useMutation.mockReturnValue({ mutate: vi.fn(), isPending: false, error: new Error("Chrome did not answer in time. Try again.") });
    const html = renderToStaticMarkup(<ConnectionsSettings />);
    const depop = html.slice(html.indexOf('id="chrome-depop"'), html.indexOf('id="connections"'));
    expect(depop).toContain("Sign-in not confirmed");
    expect(depop).toContain("Chrome did not answer in time. Try again.");
    expect(depop).not.toContain("Signed in as ada");
  });

  it("groups every marketplace at the top with a sale detection switch", () => {
    const html = renderToStaticMarkup(<ConnectionsSettings />);
    const accounts = html.indexOf('id="accounts"');
    expect(html).not.toContain("Vinted");
    expect(html).not.toContain("Sellwild");
    const connections = html.indexOf('id="connections"');
    expect(accounts).toBeGreaterThanOrEqual(0);
    expect(accounts).toBeLessThan(connections);
    const section = html.slice(accounts, connections);
    expect(section).toContain(">Accounts<");
    let cursor = 0;
    for (const name of ["eBay", "Etsy", "Poshmark", "Mercari", "Depop"]) {
      const at = section.indexOf(name, cursor);
      expect(at).toBeGreaterThanOrEqual(cursor);
      cursor = at + name.length;
    }
    for (const name of ["eBay", "Etsy", "Poshmark", "Mercari", "Depop"]) {
      expect(section).toContain(`Sale detection for ${name}`);
    }
    expect(section).toContain(">After a sale<");
    expect(section.indexOf('id="sale-detection"')).toBeGreaterThan(section.indexOf('id="after-sale"'));
    expect(section).toContain("Detect sales");
    expect(section).toContain("Where the sign-in lives");
    expect(html).toContain("Marketplaces → eBay → Offers");
    // The ships-from ZIP lives once, under Settings → Listings.
    expect(html).not.toContain('id="ebay-inventory-location"');
    expect(html).not.toContain("ZIP");
    expect(html).not.toContain("Chrome sign-in");
    expect(html).not.toContain("ebay-offers");
    expect(html).not.toContain("Automatic offers check every 15 minutes");
  });

  it("shows Detect sales off, with the reason, until the account can be checked, and keeps the saved choice", () => {
    const html = renderToStaticMarkup(<ConnectionsSettings />);
    const row = (id: string) => html.slice(html.indexOf(`id="${id}"`), html.indexOf("</div></div>", html.indexOf(`aria-label="Sale detection for`, html.indexOf(`id="${id}"`))));
    // Nothing is saved for any marketplace, so every one is on by preference.
    expect(row("direct-ebay")).toContain("Connect to detect sales");
    expect(row("chrome-poshmark")).toContain("Sign in to detect sales");
    expect(row("chrome-mercari")).toContain("Sign in to detect sales");
    expect(row("chrome-mercari")).toContain("Sign-in expired");
    expect(row("chrome-mercari")).toContain("Sign in again</button>");
    expect(row("chrome-depop")).toContain("Detect sales");
    expect(row("chrome-depop")).not.toContain("to detect sales");
    const switches = [...html.matchAll(/<[a-z]+ [^>]*aria-label="Sale detection for (\w+)"[^>]*>/g)];
    const state = Object.fromEntries(switches.map(([tag, name]) => [name, { checked: tag.includes('aria-checked="true"'), disabled: / data-disabled(="")?[ >]|aria-disabled="true"/.test(tag) }]));
    expect(state.Depop).toEqual({ checked: true, disabled: false });
    for (const name of ["eBay", "Etsy", "Poshmark", "Mercari"]) expect(state[name]).toEqual({ checked: false, disabled: true });
  });

  it("asks to connect Chrome before sales can be detected on Chrome marketplaces", () => {
    const base = useQuery.getMockImplementation()!;
    useQuery.mockImplementation((options: { queryKey: string[] }) => {
      if (options.queryKey[0] === "extension-status") return { data: { connected: false } };
      return base(options);
    });
    const html = renderToStaticMarkup(<ConnectionsSettings />);
    expect(html.match(/Connect Chrome to detect sales/g)).toHaveLength(3);
  });

  it("offers to reconnect eBay when eBay refuses the listing policies", () => {
    const base = useQuery.getMockImplementation()!;
    useQuery.mockImplementation((options: { queryKey: string[] }) => {
      if (options.queryKey[0] === "direct-connections") return { data: { accounts: { ebay: { connected: true, label: "seller" } } } };
      if (options.queryKey[0] === "ebay-listing-profiles") return { error: new Error("Reconnect eBay to grant it.") };
      return base(options);
    });
    const html = renderToStaticMarkup(<ConnectionsSettings />);
    expect(html).toContain("Reconnect eBay to grant it.");
    expect(html).toContain("Reconnect eBay</button>");
  });

  it("on the web, shows Chrome sign-ins and seller-owned sale detection switches", () => {
    const base = useQuery.getMockImplementation()!;
    useQuery.mockImplementation((options) => options.queryKey[0] === "sale-detection"
      ? { data: { autodelist: false } }
      : base(options));
    const html = renderToStaticMarkup(
      <WorkspaceRuntimeProvider value={{ ...desktopRuntime, hosted: true }}><ConnectionsSettings /></WorkspaceRuntimeProvider>,
    );
    let cursor = 0;
    for (const name of ["eBay", "Etsy", "Poshmark", "Mercari", "Depop"]) {
      const at = html.indexOf(name, cursor);
      expect(at).toBeGreaterThanOrEqual(cursor);
      cursor = at + name.length;
    }
    expect(html).not.toContain("Facebook");
    expect(html).toContain("Signed in as ada");
    expect(html).toContain("Not signed in");
    expect(html).toContain("Sign in</button>");
    expect(html).toContain("Sign-in setup");
    expect(html).toContain("Detect sales");
    expect(html).toContain("Sale detection for Poshmark");
    expect(html).toContain('id="sale-autodelist"');
    expect(html).toContain("Delist the other marketplaces");
    expect(html).toContain('aria-label="Delist the other marketplaces when something sells"');
    expect(html).not.toContain("this Mac");
  });

  it("on the web, explains each marketplace's available sign-ins and shows their own status", () => {
    const base = useQuery.getMockImplementation()!;
    useQuery.mockImplementation((options) => {
      if (options.queryKey[0] === "marketplace-sessions") {
        const browser = { state: "signed_in", expires_at: null, label: "browser-account", expiring: false };
        return { isSuccess: true, data: {
          chrome_connected: true,
          chrome_sessions: { depop: browser },
          sessions: { depop: { ...browser, mode: "cloud", label: "old-cloud-account" } },
        } };
      }
      if (options.queryKey[0] === "cloud-sessions") {
        return { data: { enabled: ["poshmark", "depop"], sessions: {
          poshmark: { state: "signed_in", cloud_connected: true, label: "poshmark-cloud", expires_at: null, expiring: false },
          depop: { state: "expired", cloud_connected: false, expires_at: null, expiring: false },
        } } };
      }
      return base(options);
    });
    const html = renderToStaticMarkup(
      <WorkspaceRuntimeProvider value={{ ...desktopRuntime, hosted: true }}><ConnectionsSettings /></WorkspaceRuntimeProvider>,
    );
    expect(html).toContain("Signed in as browser-account");
    expect(html).toContain("Sign-in expired");
    expect(html).toContain('aria-label="Sign in to Depop in Chrome"');
    expect(html).toContain('aria-label="Sign in to Depop in Cloud"');
    expect(html).toContain('aria-label="Sign in to Poshmark in Chrome"');
    expect(html).toContain('aria-label="Sign in to Poshmark in Cloud"');
    expect(html).toContain('aria-label="Sign in to Mercari in Chrome"');
    expect(html).toContain('aria-describedby="mercari-chrome-sign-in-help"');
    expect(html).not.toContain('aria-label="Sign in to Mercari in Cloud"');
    expect(html).toContain("Keep your sign-in in your browser. Leave Chrome open for listing actions and sales checks.");
    expect(html).toContain("Anteroom saves an encrypted sign-in so listing actions, sales checks, and enabled automations can run with Chrome closed.");
    expect(html).toContain("One connection per marketplace is enough.");
    expect(html).not.toContain("old-cloud-account");
  });

  it("on the web, says why Chrome's sign-ins cannot be read", () => {
    const base = useQuery.getMockImplementation()!;
    useQuery.mockImplementation((options: { queryKey: string[] }) => {
      if (options.queryKey[0] === "marketplace-sessions") {
        return { isError: true, error: new Error("Open Anteroom in Chrome on your computer and connect Chrome first.") };
      }
      return base(options);
    });
    const html = renderToStaticMarkup(
      <WorkspaceRuntimeProvider value={{ ...desktopRuntime, hosted: true }}><ConnectionsSettings /></WorkspaceRuntimeProvider>,
    );
    expect(html).toContain("Open Anteroom in Chrome on your computer and connect Chrome first.");
    expect(html).toContain("Connect Chrome first");
  });

  it("on the web, asks to update an older Bridge instead of to connect Chrome", () => {
    const base = useQuery.getMockImplementation()!;
    useQuery.mockImplementation((options: { queryKey: string[] }) => {
      if (options.queryKey[0] === "marketplace-sessions") {
        return { isError: true, error: new Error("Update Anteroom Bridge in Chrome to 0.2.44 or later to check marketplace sign-in from the web.") };
      }
      return base(options);
    });
    const html = renderToStaticMarkup(
      <WorkspaceRuntimeProvider value={{ ...desktopRuntime, hosted: true }}><ConnectionsSettings /></WorkspaceRuntimeProvider>,
    );
    expect(html).toContain("Update Anteroom Bridge in Chrome to 0.2.44 or later");
    expect(html).toContain("Update Anteroom Bridge<");
    expect(html).not.toContain("Connect Chrome first");
    expect(html).toContain("https://chromewebstore.google.com/detail/empinkdlhndpfoigoipbiiofaeepcheo");
  });

  it("links to the Chrome Web Store on desktop Connections settings", () => {
    const html = renderToStaticMarkup(<ConnectionsSettings />);
    expect(html).toContain("https://chromewebstore.google.com/detail/empinkdlhndpfoigoipbiiofaeepcheo");
    expect(html).toContain("Chrome Web Store");
  });

  it("shows loading without an off switch while the saved autodelist preference is unknown", () => {
    const html = renderToStaticMarkup(
      <WorkspaceRuntimeProvider value={{ ...desktopRuntime, hosted: true }}><ConnectionsSettings /></WorkspaceRuntimeProvider>,
    );
    expect(html).toMatch(/role="status"[^>]*>Loading…<\/span>/);
    expect(html).not.toContain('aria-label="Delist the other marketplaces when something sells"');
  });

  it("shows the load error without an off switch when the saved autodelist preference is unknown", () => {
    const base = useQuery.getMockImplementation()!;
    useQuery.mockImplementation((options: { queryKey: string[] }) => {
      if (options.queryKey[0] === "sale-detection") {
        return { data: undefined, error: new Error("Could not load sale detection settings.") };
      }
      return base(options);
    });
    const html = renderToStaticMarkup(
      <WorkspaceRuntimeProvider value={{ ...desktopRuntime, hosted: true }}><ConnectionsSettings /></WorkspaceRuntimeProvider>,
    );
    expect(html).toContain(">Unavailable</span>");
    expect(html).toContain("Could not load sale detection settings.");
    expect(html).not.toContain('aria-label="Delist the other marketplaces when something sells"');
  });

  it("shows the saved autodelist preference and indicates when it is saving", () => {
    const base = useQuery.getMockImplementation()!;
    useQuery.mockImplementation((options) => options.queryKey[0] === "sale-detection"
      ? { data: { autodelist: true } }
      : base(options));
    const render = () => renderToStaticMarkup(
      <WorkspaceRuntimeProvider value={{ ...desktopRuntime, hosted: true }}><ConnectionsSettings /></WorkspaceRuntimeProvider>,
    );
    const findSwitch = (html: string) => html.match(/<[^>]+aria-label="Delist the other marketplaces when something sells"[^>]*>/)?.[0];
    const loaded = render();
    expect(findSwitch(loaded)).toContain('aria-checked="true"');
    expect(findSwitch(loaded)).not.toMatch(/\sdata-disabled(?:=|\s|>)/);
    expect(loaded).not.toContain("Saving…");

    useMutation.mockReturnValue({ mutate: vi.fn(), isPending: true, error: null });
    const saving = render();
    expect(findSwitch(saving)).toContain('aria-checked="true"');
    expect(findSwitch(saving)).toMatch(/\sdata-disabled(?:=|\s|>)/);
    expect(saving.slice(saving.indexOf('id="sale-autodelist"'))).toMatch(/role="status"[^>]*>.*?Saving…/);
  });
});

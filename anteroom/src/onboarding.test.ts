import { afterEach, expect, test, vi } from "vitest";
import { dismissSetupGuide, isSetupGuideDismissed } from "./onboarding";
import { api } from "./api/client";
import { hasHostedApiSession } from "./api/transport";

vi.mock("./api/client", () => ({ api: { settings: { dismissSetupGuide: vi.fn().mockResolvedValue({ ok: true }) } } }));
vi.mock("./api/transport", () => ({ hasHostedApiSession: vi.fn() }));
afterEach(() => { vi.clearAllMocks(); vi.unstubAllGlobals(); });

test("hosted onboarding saves locally without calling the desktop endpoint", () => {
  const setItem = vi.fn();
  vi.stubGlobal("localStorage", { setItem });
  vi.mocked(hasHostedApiSession).mockReturnValue(true);
  dismissSetupGuide();
  expect(setItem).toHaveBeenCalledWith("anteroom.setup-guide", "done");
  expect(api.settings.dismissSetupGuide).not.toHaveBeenCalled();
});

test("desktop onboarding still persists its dismissal to the server", () => {
  vi.mocked(hasHostedApiSession).mockReturnValue(false);
  dismissSetupGuide();
  expect(api.settings.dismissSetupGuide).toHaveBeenCalledOnce();
});

test("recognizes pre-rename vendoo-studio.setup-guide dismissal", () => {
  const store = new Map([["vendoo-studio.setup-guide", "done"]]);
  vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null });
  expect(isSetupGuideDismissed()).toBe(true);
});

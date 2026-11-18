// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { MacAccountButton } from "./MacAccountButton";

const readMove = vi.hoisted(() => vi.fn());
const startMove = vi.hoisted(() => vi.fn());
const destination = vi.hoisted(() => vi.fn());
vi.mock("../api/accountMove", () => ({ getAccountMove: readMove, startAccountMove: startMove, getAccountDestination: destination }));
vi.mock("../lib/macApp", () => ({ useMacApp: () => true }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => { document.body.innerHTML = ""; vi.clearAllMocks(); });

it("keeps account connection guidance separate from the explicit listing copy flow", async () => {
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  readMove.mockResolvedValue({ hosted_url: "https://myanteroom.com/?sign-in", listings: 10, photos: 20,
    moved: null, progress: { state: "idle" } });
  destination.mockResolvedValue({ hosted_url: "https://staging.example.test/?sign-in" });
  const button = (name: string) => Array.from(document.querySelectorAll("button"))
    .find((node) => node.textContent?.trim() === name)!;
  const navigate = vi.spyOn(window.location, "assign").mockImplementation(() => {});
  try {
    await act(async () => root.render(<QueryClientProvider client={client}><MacAccountButton /></QueryClientProvider>));
    expect(button("My Account")).toBeDefined();
    expect(destination).not.toHaveBeenCalled();
    await act(async () => button("My Account").click());
    await vi.waitFor(async () => {
      await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
      expect(button("Open My Account").disabled).toBe(false);
    });
    expect(document.body.textContent).toContain("Provider sign-in on this Mac is separate");
    expect(button("Open My Account")).toBeDefined();
    expect(document.querySelector('input[type="password"]')).toBeNull();
    expect(readMove).not.toHaveBeenCalled();
    expect(startMove).not.toHaveBeenCalled();
    await act(async () => button("Open My Account").click());
    expect(navigate).toHaveBeenCalledWith("https://staging.example.test/?sign-in");
    await act(async () => button("Copy this Mac’s listings…").click());
    await vi.waitFor(async () => {
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
      expect(readMove).toHaveBeenCalledTimes(1);
      expect(document.querySelector('input[type="password"]')).not.toBeNull();
    });
    expect(startMove).not.toHaveBeenCalled();
  } finally { await act(async () => root.unmount()); client.clear(); navigate.mockRestore(); }
});

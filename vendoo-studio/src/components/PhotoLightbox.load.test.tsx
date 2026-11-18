// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { desktopRuntime, WorkspaceRuntimeProvider } from "../app/runtime";
import { installHostedApiSession } from "../api/transport";
import { PhotoLightbox } from "./PhotoThumbs";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>();
let uninstall: () => void = () => {};
let created = 0;

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => `blob:shown-${++created}`, revokeObjectURL: vi.fn() }));
  fetchMock.mockReset();
  uninstall = installHostedApiSession({
    seller: "alice", origin: "https://anteroom.test", accessToken: async () => "token", unauthorized: () => {},
  });
});

afterEach(() => { uninstall(); vi.unstubAllGlobals(); document.body.innerHTML = ""; });

/** The viewer as a hosted seller sees it: the full-size photo comes from a signed-in download. */
async function openViewer(src: string) {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(
    <WorkspaceRuntimeProvider value={{ ...desktopRuntime, hosted: true }}>
      <PhotoLightbox urls={[src]} index={0} onStep={() => {}} onClose={() => {}} />
    </WorkspaceRuntimeProvider>,
  ));
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  return root;
}

const failure = () => document.body.querySelector<HTMLElement>("[data-photo-load-status]");
const photo = () => document.body.querySelector<HTMLImageElement>('img[alt="Photo 1"]');

it("says a full-size photo could not load, and Retry downloads it again", async () => {
  fetchMock.mockResolvedValueOnce(new Response("", { status: 404 }));
  const root = await openViewer("/api/conversations/c/photos/missing/bytes?v=1");
  try {
    expect(failure()?.textContent).toContain("Couldn't load this photo.");
    expect(failure()?.dataset.photoLoadStatus).toBe("404");
    // No silent picture with only its alt text.
    expect(photo()).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    fetchMock.mockResolvedValueOnce(new Response(new Blob(["jpeg"]), { status: 200 }));
    const retry = [...document.body.querySelectorAll("button")].find((button) => button.textContent === "Retry")!;
    await act(async () => { retry.click(); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(failure()).toBeNull();
    expect(photo()?.getAttribute("src")).toMatch(/^blob:shown-/);
  } finally { await act(async () => root.unmount()); }
});

it("asks the seller to sign in again when the download is refused for their session", async () => {
  fetchMock.mockResolvedValueOnce(new Response("", { status: 401 }));
  const root = await openViewer("/api/conversations/c/photos/session/bytes?v=1");
  try {
    expect(failure()?.dataset.photoLoadStatus).toBe("401");
    expect(failure()?.textContent).toContain("Sign in again");
    expect(failure()?.textContent).not.toMatch(/\/api\/|session\b/);
  } finally { await act(async () => root.unmount()); }
});

it("downloads again on Retry when a photo that arrived cannot be shown", async () => {
  fetchMock.mockResolvedValueOnce(new Response(new Blob(["not an image"]), { status: 200 }));
  const root = await openViewer("/api/conversations/c/photos/unreadable/bytes?v=1");
  try {
    expect(photo()?.getAttribute("src")).toMatch(/^blob:shown-/);
    // The browser could not decode the file it was given.
    await act(async () => { photo()!.dispatchEvent(new Event("error")); });
    expect(failure()?.textContent).toContain("Couldn't load this photo.");
    expect(failure()?.dataset.photoLoadStatus).toBe("none");
    expect(photo()).toBeNull();

    fetchMock.mockResolvedValueOnce(new Response(new Blob(["jpeg"]), { status: 200 }));
    const retry = [...document.body.querySelectorAll("button")].find((button) => button.textContent === "Retry")!;
    await act(async () => { retry.click(); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    // The unreadable copy is not reused: Retry asks the server again.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(photo()?.getAttribute("src")).toMatch(/^blob:shown-/);
  } finally { await act(async () => root.unmount()); }
});

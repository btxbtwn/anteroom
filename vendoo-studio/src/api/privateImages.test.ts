import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cachedPrivateImage, loadPrivateImage } from "./privateImages";
import { installHostedApiSession } from "./transport";

let uninstall: () => void = () => {};
let created = 0;
const fetchMock = vi.fn(async () => new Response(new Blob(["jpeg"]), { status: 200 }));

function signIn() {
  uninstall();
  uninstall = installHostedApiSession({
    origin: "https://anteroom.test", accessToken: async () => "token", unauthorized: () => {},
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("URL", Object.assign(URL, {
    createObjectURL: () => `blob:${++created}`,
    revokeObjectURL: vi.fn(),
  }));
  fetchMock.mockClear();
  signIn();
});

afterEach(() => { uninstall(); vi.unstubAllGlobals(); });

it("loads a cover once and shows it again without downloading it", async () => {
  const src = "/api/photos/p1/thumb?size=96&v=abc";
  const first = await loadPrivateImage(src);
  expect(cachedPrivateImage(src)).toBe(first);
  expect(await loadPrivateImage(src)).toBe(first);
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

it("forgets the last account's photos when the account changes", async () => {
  const src = "/api/photos/p2/thumb?size=96&v=abc";
  await loadPrivateImage(src);
  signIn();
  expect(cachedPrivateImage(src)).toBeUndefined();
  await loadPrivateImage(src);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

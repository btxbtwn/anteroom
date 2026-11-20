import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { desktopRuntime, WorkspaceRuntimeProvider } from "../app/runtime";
import { canAddPhotos } from "./photoAdding";
import { PhotoTray } from "./PhotoTray";

function tray(hosted: boolean, status: string) {
  const client = new QueryClient();
  client.setQueryData(["photos", "c1"], []);
  client.setQueryData(["conversation", "c1"], { id: "c1", status });
  return renderToStaticMarkup(
    <WorkspaceRuntimeProvider value={{ ...desktopRuntime, hosted }}>
      <QueryClientProvider client={client}><PhotoTray convId="c1" /></QueryClientProvider>
    </WorkspaceRuntimeProvider>,
  );
}

it("offers adding photos to a hosted draft", () => {
  const html = tray(true, "draft");
  expect(html).toContain("Add Photos");
  expect(html).toContain("Add Folder");
  expect(html).not.toContain("while this listing is a draft");
});

it("on the website, a listing that is no longer a draft explains instead of offering uploads that cannot work", () => {
  const html = tray(true, "sold");
  expect(html).not.toContain("Add Photos");
  expect(html).not.toContain("Add Folder");
  expect(html).not.toContain('type="file"');
  expect(html).toContain("New photos can be added while this listing is a draft.");
});

it("the Mac app adds photos to any listing", () => {
  const html = tray(false, "sold");
  expect(html).toContain("Add Photos");
  expect(html).not.toContain("while this listing is a draft");
});

it("hosted uploads only reach drafts; an unread status does not hide the buttons", () => {
  expect(canAddPhotos(true, "draft")).toBe(true);
  expect(canAddPhotos(true, "active")).toBe(false);
  expect(canAddPhotos(true, "sold")).toBe(false);
  expect(canAddPhotos(true, undefined)).toBe(true);
  expect(canAddPhotos(false, "sold")).toBe(true);
});

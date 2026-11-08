import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { WorkspaceTopbar } from "./WorkspaceTopbar";

function render(sidebarOpen: boolean) {
  return renderToStaticMarkup(
    <WorkspaceTopbar
      crumbs={{ context: "Analytics", title: null }}
      sidebarOpen={sidebarOpen}
      onToggleSidebar={() => {}}
      rightPanel={null}
    />,
  );
}

describe("WorkspaceTopbar", () => {
  it("shows the Anteroom logo over the open sidebar", () => {
    const html = render(true);
    expect(html).toContain("<svg");
    expect(html).toContain("Anteroom");
  });

  it("drops the logo when the sidebar is hidden", () => {
    expect(render(false)).not.toContain("Anteroom");
  });
});

import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { SuggestionsButton } from "./SuggestionsButton";

it("offers a named button to reopen suggestions without closing the current listing", () => {
  const html = renderToStaticMarkup(<SuggestionsButton onSelect={() => {}} />);
  expect(html).toContain("Suggestions");
  expect(html).toContain('aria-haspopup="dialog"');
});

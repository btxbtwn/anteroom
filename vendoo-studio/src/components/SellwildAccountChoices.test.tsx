import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it } from "vitest";
import { SellwildAccountChoices } from "./SellwildAccountChoices";

it.each([true, false])("shows the sign-in prompt only while disconnected (%s)", (connected) => {
  const html = renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}>
    <SellwildAccountChoices convId="test" connected={connected} />
  </QueryClientProvider>);
  expect(html.includes("Sign in to Sellwild in Anteroom")).toBe(!connected);
  expect(html.includes("Sellwild is signed in.")).toBe(connected);
  expect(html).toContain("Reload Sellwild choices");
});

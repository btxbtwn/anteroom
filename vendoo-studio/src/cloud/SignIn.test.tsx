// @vitest-environment happy-dom
import type { SupabaseClient } from "@supabase/supabase-js";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { SetPassword, SignIn } from "./SignIn";

const client = {} as SupabaseClient;

it("names every app without an uppercase eyebrow, and the submit button is the primary action", () => {
  const html = renderToStaticMarkup(<SignIn client={client} />);
  expect(html).toContain("One account for your listings on the web, on iPhone and on desktop.");
  expect(html).not.toMatch(/Mac\b/);
  expect(html).not.toContain("YOUR LISTING WORKSPACE");
  expect(html).not.toContain("eyebrow");
  expect(html).toMatch(/<button class="primary"[^>]*>Sign in<\/button>/);
});

it("keeps the local sandbox hint", () => {
  expect(renderToStaticMarkup(<SignIn client={client} sandbox />)).toContain("alice@sandbox.test");
});

it("asks for a new password without an eyebrow", () => {
  const html = renderToStaticMarkup(<SetPassword client={client} onDone={() => {}} />);
  expect(html).toContain("Choose a new password");
  expect(html).not.toContain("eyebrow");
});

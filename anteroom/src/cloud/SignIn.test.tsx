// @vitest-environment happy-dom
import type { SupabaseClient } from "@supabase/supabase-js";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { ConnectorPage } from "./ConnectorPage";
import { SetPassword, SignIn } from "./SignIn";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

it("wraps hosted auth and connector screens in a vertical scroll container", () => {
  expect(renderToStaticMarkup(<SignIn client={client} />)).toContain('class="cloud-auth h-full overflow-y-auto"');
  expect(renderToStaticMarkup(<SetPassword client={client} onDone={() => {}} />)).toContain('class="cloud-auth h-full overflow-y-auto"');
  expect(renderToStaticMarkup(<ConnectorPage email="a@example.com" onSignOut={async () => {}} />)).toContain('class="h-full overflow-y-auto"');
});

it("renders sign-in errors with destructive alert styling instead of muted text", async () => {
  const failing = {
    auth: { signInWithPassword: vi.fn().mockResolvedValue({ error: { message: "Invalid login credentials" } }) },
  } as unknown as SupabaseClient;
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(() => root.render(<SignIn client={failing} />));
    const form = container.querySelector("form")!;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    const alert = container.querySelector('[role="alert"]');
    expect(alert).not.toBeNull();
    expect(alert?.className).toContain("text-destructive-foreground");
  } finally {
    await act(() => root.unmount());
    container.remove();
  }
});

it("signup instructions name only the supported browser marketplaces", () => {
  const before = window.location.href;
  window.history.replaceState(null, "", "?create-account");
  try {
    const html = renderToStaticMarkup(<SignIn client={client} sandbox />);
    expect(html).toContain("Create your account");
    for (const name of ["Poshmark", "Mercari", "Depop"]) expect(html).toContain(name);
    expect(html).not.toContain("Vinted");
    expect(html).not.toContain("Sellwild");
    expect(html).toContain('href="/terms"');
    expect(html).toContain('href="/privacy"');
  } finally { window.history.replaceState(null, "", before); }
});

it("links Privacy, Terms, and Support from the landing page footer", async () => {
  const { Landing } = await import("./Landing");
  const html = renderToStaticMarkup(<Landing />);
  expect(html).toContain('href="/privacy"');
  expect(html).toContain('href="/terms"');
  expect(html).toContain('href="mailto:support@myanteroom.com"');
});

it("offers Resend confirmation email after email_not_confirmed and calls supabase.auth.resend", async () => {
  const resend = vi.fn().mockResolvedValue({ error: null });
  const unconfirmed = {
    auth: {
      signInWithPassword: vi.fn().mockResolvedValue({ error: { code: "email_not_confirmed" } }),
      resend,
    },
  } as unknown as SupabaseClient;
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await act(() => root.render(<SignIn client={unconfirmed} />));
    const emailInput = container.querySelector<HTMLInputElement>("#email")!;
    const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    await act(() => {
      setValue.call(emailInput, "seller@example.com");
      emailInput.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const form = container.querySelector("form")!;
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    const resendButton = Array.from(container.querySelectorAll("button")).find(
      (btn) => btn.textContent === "Resend confirmation email",
    );
    expect(resendButton).toBeDefined();
    await act(async () => {
      resendButton!.click();
    });
    expect(resend).toHaveBeenCalledWith({
      type: "signup",
      email: "seller@example.com",
      options: { emailRedirectTo: window.location.origin },
    });
    expect(container.querySelector('[role="status"]')?.textContent).toContain("seller@example.com");
  } finally {
    await act(() => root.unmount());
    container.remove();
  }
});

// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { Expense } from "../api/contract.gen";
import {
  completeConfirmDialogClose,
  readConfirmDialogState,
  registerConfirmDialogHost,
  respondToConfirmDialog,
} from "../ui/confirmDialog";
import { BusinessExpenses } from "./BusinessExpenses";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  completeConfirmDialogClose();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const manualExpense: Expense = {
  id: "exp-1",
  incurred_date: "2026-11-20",
  category: "shipping_supplies",
  accounting_type: "operating_expense",
  amount_cents: 1234,
  currency: "USD",
  vendor: "USPS",
  description: "Poly mailers",
  notes: "",
  import_source: "manual",
  revision: 1,
};

describe("BusinessExpenses confirmation", () => {
  it("uses confirmDialog with a 'Delete expense' label instead of window.confirm", async () => {
    const nativeConfirm = vi.fn().mockReturnValue(true);
    vi.stubGlobal("confirm", nativeConfirm);
    vi.spyOn(api.businessExpenses, "list").mockResolvedValue([manualExpense]);
    vi.spyOn(api.plaid, "status").mockResolvedValue({
      configured: false,
      max_connections: 2,
      connections: [],
    });
    const removeSpy = vi.spyOn(api.businessExpenses, "remove").mockResolvedValue(undefined);
    const unregister = registerConfirmDialogHost();

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } },
    });
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    try {
      await act(async () => {
        root.render(
          <QueryClientProvider client={client}>
            <BusinessExpenses report={false} mode="accrual" setMode={() => {}} />
          </QueryClientProvider>,
        );
      });

      await vi.waitFor(() => {
        expect(host.textContent).toContain("Poly mailers");
      });

      const editButton = [...host.querySelectorAll<HTMLButtonElement>("button")].find(
        (button) => button.textContent?.trim() === "Edit",
      );
      await act(async () => {
        editButton!.click();
      });

      const deleteButton = [...host.querySelectorAll<HTMLButtonElement>("button")].find(
        (button) => button.textContent?.trim() === "Delete expense",
      );

      // Cancel first: should not delete.
      await act(async () => {
        deleteButton!.click();
      });
      expect(nativeConfirm).not.toHaveBeenCalled();
      expect(removeSpy).not.toHaveBeenCalled();
      let dialog = readConfirmDialogState();
      expect(dialog.status).toBe("confirming");
      expect(dialog.status === "confirming" && dialog.confirmLabel).toBe("Delete expense");
      expect(dialog.status === "confirming" && dialog.variant).toBe("destructive");
      await act(async () => {
        respondToConfirmDialog(false);
        completeConfirmDialogClose();
      });
      expect(removeSpy).not.toHaveBeenCalled();

      // Confirm: deletes expense.
      await act(async () => {
        deleteButton!.click();
      });
      dialog = readConfirmDialogState();
      expect(dialog.status).toBe("confirming");
      await act(async () => {
        respondToConfirmDialog(true);
        completeConfirmDialogClose();
      });

      await vi.waitFor(() => {
        expect(removeSpy).toHaveBeenCalledWith(manualExpense, expect.anything());
      });
    } finally {
      unregister();
      await act(async () => root.unmount());
      client.clear();
      host.remove();
    }
  });
});

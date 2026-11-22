import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { CsvResult, Expense, ExpenseInput, MileageInput } from "../api/contract.gen";
import { downloadFile } from "./downloadFile";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { EmptyRow, FormError, FormField, PageHeader, PageSkeleton, Panel } from "./WorkspacePage";

const CATEGORIES: Record<ExpenseInput["category"], string> = {
  inventory_purchases: "Inventory purchases",
  sourcing_transportation: "Sourcing transportation",
  shipping_supplies: "Shipping supplies",
  shipping_labels: "Shipping labels",
  platform_fees: "Platform fees",
  software_subscriptions: "Software subscriptions",
  equipment: "Equipment",
  home_office: "Home office",
  advertising: "Advertising",
  mileage: "Mileage",
  other: "Other",
};
const categoryOptions = Object.keys(CATEGORIES) as ExpenseInput["category"][];
const path = "/api/business-expenses";

export function expenseMoney(cents: number, currency: string) {
  return `${currency} ${(cents / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function Choice<T extends string>({ label, value, options, onChange }: {
  label: string;
  value: T;
  options: readonly T[];
  onChange: (value: T) => void;
}) {
  const display = (key: T) => CATEGORIES[key as ExpenseInput["category"]] ?? key;
  return <Select value={value} onValueChange={(next) => { if (next) onChange(next); }}>
    <SelectTrigger aria-label={label} className="w-auto min-w-28 max-w-full"><SelectValue>{display(value)}</SelectValue></SelectTrigger>
    <SelectPopup>{options.map((key) => <SelectItem key={key} value={key}>{display(key)}</SelectItem>)}</SelectPopup>
  </Select>;
}

export type AccountingMode = "accrual" | "cash";

export function AccountingChoice({ mode, setMode }: { mode: AccountingMode; setMode: (mode: AccountingMode) => void }) {
  return <Choice label="Accounting mode" value={mode === "accrual" ? "Accrual" : "Cash"}
    options={["Accrual", "Cash"]} onChange={(value) => setMode(value === "Accrual" ? "accrual" : "cash")} />;
}

export function BusinessExpenses({ report, mode, setMode }: {
  report: boolean;
  mode: AccountingMode;
  setMode: (mode: AccountingMode) => void;
}) {
  const [today] = useState(() => new Date().toISOString().slice(0, 10));
  const [start, setStart] = useState(`${today.slice(0, 4)}-01-01`);
  const [end, setEnd] = useState(today);
  const [page, setPage] = useState(1);
  const [category, setCategory] = useState<ExpenseInput["category"] | "All categories">("All categories");
  const [editing, setEditing] = useState<Expense | "new" | null>(null);
  const [downloadError, setDownloadError] = useState("");
  const valid = Boolean(start && end && start <= end);
  const dates = new URLSearchParams({ start, end }).toString();
  const expenseParams = new URLSearchParams({ start, end, page: String(page) });
  if (category !== "All categories") expenseParams.set("category", category);
  const expenses = useQuery({
    queryKey: ["business-expenses", expenseParams.toString()],
    queryFn: ({ signal }) => api.businessExpenses.list(expenseParams.toString(), signal),
    enabled: valid && !report,
  });
  const reportParams = `${dates}&mode=${mode}`;
  const pnl = useQuery({
    queryKey: ["profit-and-loss", reportParams],
    queryFn: ({ signal }) => api.businessExpenses.report(reportParams, signal),
    enabled: valid && report,
  });
  const active = report ? pnl : expenses;
  const download = (url: string, filename: string) => {
    setDownloadError("");
    void downloadFile(url, filename).catch((error: unknown) => setDownloadError(String(error)));
  };
  return <>
    <PageHeader>
      <h2 className="text-xl font-semibold">{report ? "Profit & loss" : "Business expenses"}</h2>
      <div className="flex flex-wrap items-end gap-2">
        <FormField label="From"><Input type="date" value={start} onChange={(event) => { setStart(event.target.value); setPage(1); }} /></FormField>
        <FormField label="Through"><Input type="date" value={end} max={today} onChange={(event) => { setEnd(event.target.value); setPage(1); }} /></FormField>
        {report ? <AccountingChoice mode={mode} setMode={setMode} /> :
          <Choice label="Filter by category" value={category} options={["All categories", ...categoryOptions]}
            onChange={(value) => { setCategory(value); setPage(1); }} />}
        <Button variant="outline" disabled={!valid} onClick={() => download(
          report ? `${path}/profit-and-loss.csv?${reportParams}` : `${path}/expenses.csv?${expenseParams}`,
          report ? "profit-and-loss.csv" : "expenses.csv",
        )}>Export CSV</Button>
        {!report && <Button onClick={() => setEditing("new")}>Add expense</Button>}
      </div>
    </PageHeader>
    {downloadError && <FormError>{downloadError}</FormError>}
    {!valid && <FormError>Choose a valid date range.</FormError>}
    {!report && <>
      {editing && <ExpenseForm key={editing === "new" ? "new" : editing.id}
        expense={editing === "new" ? undefined : editing} today={today} close={() => setEditing(null)} />}
      <ExpenseImport downloadTemplate={() => download(`${path}/template.csv`, "expense-template.csv")} />
    </>}
    {valid && active.isPending && <PageSkeleton />}
    {valid && active.error && <FormError>{active.error.message}</FormError>}
    {valid && (report ? pnl.data?.length === 0 : expenses.data?.length === 0) && <EmptyRow>No records in this period.</EmptyRow>}
    {report ? pnl.data?.map((currency) => <Panel key={currency.currency}>
      <div className="space-y-2 p-3 sm:p-4">
        <h3 className="text-sm font-medium">{currency.currency}</h3>
        {currency.incomplete_sales > 0 && <p className="text-xs text-muted-foreground">
          {currency.incomplete_sales} sales have missing or out-of-date figures. Totals use recorded figures.
        </p>}
        <dl className="divide-y divide-border/60 text-sm">{currency.lines.map((line) =>
          <div key={line.label} className="flex justify-between gap-3 py-2">
            <dt>{line.label}</dt><dd className="tabular-nums">{expenseMoney(line.amount_cents, currency.currency)}</dd>
          </div>)}</dl>
      </div>
    </Panel>) : <>
      {expenses.data?.map((expense) => <Panel key={expense.id}>
        <div className="flex flex-wrap items-center justify-between gap-2 p-3 sm:p-4">
          <div className="min-w-0 break-words text-sm">
            <p>{expense.description || expense.vendor || CATEGORIES[expense.category]}</p>
            <p className="text-xs text-muted-foreground">{expense.incurred_date} · {CATEGORIES[expense.category]}</p>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <span className="tabular-nums">{expenseMoney(expense.amount_cents, expense.currency ?? "USD")}</span>
            <Button variant="ghost" onClick={() => setEditing(expense)}>Edit</Button>
          </div>
        </div>
      </Panel>)}
      {(page > 1 || expenses.data?.length === 50) && <div className="flex gap-2">
        <Button variant="outline" disabled={page === 1} onClick={() => setPage(page - 1)}>Previous</Button>
        <Button variant="outline" disabled={expenses.data?.length !== 50} onClick={() => setPage(page + 1)}>Next</Button>
      </div>}
    </>}
  </>;
}

function useRefreshExpenses() {
  const queryClient = useQueryClient();
  return () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["business-expenses"] }),
    queryClient.invalidateQueries({ queryKey: ["profit-and-loss"] }),
  ]);
}

function ExpenseForm({ expense, today, close }: { expense?: Expense; today: string; close: () => void }) {
  const [category, setCategory] = useState(expense?.category ?? "other");
  const [country, setCountry] = useState<MileageInput["country"]>(expense?.currency === "CAD" ? "CA" : "US");
  const [unit, setUnit] = useState<MileageInput["distance_unit"]>(expense?.distance_unit ?? "mi");
  const refresh = useRefreshExpenses();
  const calculate = (form: FormData) => api.businessExpenses.mileage({
    incurred_date: String(form.get("date") ?? ""),
    country, distance: String(form.get("distance") ?? ""), distance_unit: unit,
    prior_business_km: String(form.get("prior") || "0"), northern_territory: form.has("northern"),
    us_rate_override_cents: String(form.get("rate") ?? "") || undefined,
  });
  const preview = useMutation({ mutationFn: calculate });
  const save = useMutation({
    mutationFn: async (form: FormData) => {
      const value = (key: string) => String(form.get(key) ?? "");
      const body: ExpenseInput = {
        incurred_date: value("date"), currency: value("currency").toUpperCase(), category,
        amount_cents: Math.round(Number(value("amount")) * 100),
        vendor: value("vendor"), description: value("description"), notes: value("notes"),
      };
      if (category === "mileage") {
        const distance = value("distance");
        const unchanged = expense?.category === "mileage" && expense.incurred_date === body.incurred_date
          && String(expense.distance) === distance && expense.distance_unit === unit
          && (expense.currency === "CAD" ? "CA" : "US") === country && !value("prior") && !form.has("northern") && !value("rate");
        const amount = unchanged ? {
          amount_cents: expense.amount_cents, currency: expense.currency,
          mileage_rate_cents: expense.mileage_rate_cents,
        } : await calculate(form);
        Object.assign(body, amount, { distance, distance_unit: unit, origin_destination: value("route") });
      }
      return api.businessExpenses.save(body, expense?.id);
    },
    onSuccess: async () => { await refresh(); close(); },
  });
  const remove = useMutation({
    mutationFn: api.businessExpenses.remove,
    onSuccess: async () => { await refresh(); close(); },
  });
  const busy = save.isPending || remove.isPending || preview.isPending;
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    save.mutate(new FormData(event.currentTarget));
  };
  return <Panel>
    <form className="space-y-3 p-3 sm:p-4" onSubmit={submit} onChange={() => { if (preview.data || preview.error) preview.reset(); }}>
      <h3 className="text-sm font-medium">{expense ? "Edit expense" : "Add expense"}</h3>
      <fieldset disabled={busy} className="grid gap-3 sm:grid-cols-2">
        <FormField label="Date"><Input type="date" name="date" defaultValue={expense?.incurred_date ?? today} required /></FormField>
        <Choice label="Category" value={category} options={categoryOptions} onChange={(value) => { setCategory(value); preview.reset(); }} />
        {category === "mileage" ? <>
          <Choice label="Rate schedule" value={country} options={["US", "CA"]} onChange={(value) => { setCountry(value); preview.reset(); }} />
          <Choice label="Distance unit" value={unit} options={["mi", "km"]} onChange={(value) => { setUnit(value); preview.reset(); }} />
          <FormField label="Distance"><Input name="distance" type="number" min="0.001" step="0.001" defaultValue={String(expense?.distance ?? "")} required /></FormField>
          <FormField label="Origin and destination"><Input name="route" defaultValue={expense?.origin_destination ?? ""} /></FormField>
          {country === "US" && <FormField label={`Custom rate in cents per ${unit} (optional)`}>
            <Input name="rate" type="number" min="0.000001" step="0.000001" />
          </FormField>}
          {country === "CA" && <>
            <FormField label="Prior business km this year"><Input name="prior" type="number" min="0" step="0.001" placeholder="0" /></FormField>
            <CanadianRegion onChange={preview.reset} />
          </>}
        </> : <>
          <FormField label="Amount"><Input name="amount" type="number" min="0.01" step="0.01" defaultValue={expense ? expense.amount_cents / 100 : undefined} required /></FormField>
          <FormField label="Currency"><Input name="currency" pattern="[A-Za-z]{3}" defaultValue={expense?.currency ?? "USD"} required /></FormField>
        </>}
        <FormField label="Vendor"><Input name="vendor" defaultValue={expense?.vendor} maxLength={256} /></FormField>
        <FormField label="Description"><Input name="description" defaultValue={expense?.description} maxLength={512} /></FormField>
        <FormField label="Notes"><Input name="notes" defaultValue={expense?.notes} maxLength={4096} /></FormField>
      </fieldset>
      {(save.error || remove.error || preview.error) && <FormError>{(save.error ?? remove.error ?? preview.error)?.message}</FormError>}
      {category === "mileage" && preview.data && <p role="status" className="text-sm tabular-nums">
        Mileage estimate {expenseMoney(preview.data.amount_cents, preview.data.currency)}
      </p>}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={busy}>{save.isPending ? "Saving…" : "Save expense"}</Button>
        {category === "mileage" && <Button type="button" variant="outline" disabled={busy} onClick={(event) => {
          const form = event.currentTarget.form;
          if (form?.reportValidity()) preview.mutate(new FormData(form));
        }}>Calculate mileage</Button>}
        <Button type="button" variant="outline" disabled={busy} onClick={close}>Cancel</Button>
        {expense && <Button type="button" variant="ghost" disabled={busy}
          onClick={() => { if (window.confirm("Delete this expense?")) remove.mutate(expense.id); }}>Delete expense</Button>}
      </div>
    </form>
  </Panel>;
}

function CanadianRegion({ onChange }: { onChange: () => void }) {
  const [region, setRegion] = useState("Province");
  return <div>
    <Choice label="Canadian region" value={region} options={["Province", "Northern territory"]}
      onChange={(value) => { setRegion(value); onChange(); }} />
    {region === "Northern territory" && <input type="hidden" name="northern" value="yes" />}
  </div>;
}

function ExpenseImport({ downloadTemplate }: { downloadTemplate: () => void }) {
  const [file, setFile] = useState<File>();
  const [result, setResult] = useState<CsvResult>();
  const refresh = useRefreshExpenses();
  const upload = useMutation({
    mutationFn: async (preview: boolean) => {
      if (!file) throw new Error("Choose a CSV file.");
      if (file.size > 1_000_000) throw new Error("Choose a CSV file under 1 MB.");
      return api.businessExpenses.import({ content: await file.text() }, preview);
    },
    onSuccess: async (response, preview) => {
      setResult(response);
      if (!preview) await refresh();
    },
  });
  return <Panel>
    <div className="space-y-2 p-3 sm:p-4">
      <div className="flex flex-wrap items-end gap-2">
        <FormField label="Import expense CSV"><Input type="file" accept=".csv,text/csv" disabled={upload.isPending}
          onChange={(event) => { setFile(event.target.files?.[0]); setResult(undefined); upload.reset(); }} /></FormField>
        <Button variant="outline" onClick={downloadTemplate}>Download template</Button>
        <Button variant="outline" disabled={!file || upload.isPending} onClick={() => upload.mutate(true)}>Preview</Button>
        {result && result.valid > 0 && result.imported === 0 && result.issues.length === 0 &&
          <Button disabled={upload.isPending} onClick={() => upload.mutate(false)}>Import {result.valid} expenses</Button>}
      </div>
      {upload.error && <FormError>{upload.error.message}</FormError>}
      {result && <p role="status" className="text-xs text-muted-foreground">
        {result.imported ? `${result.imported} imported` : `${result.valid} ready to import`} · {result.duplicates} duplicates skipped
      </p>}
      {result?.issues.map((issue, index) => <FormError key={index}>Row {issue.row} · {issue.detail}</FormError>)}
    </div>
  </Panel>;
}

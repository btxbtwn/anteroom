import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { ScoutCheck, ScoutState } from "../api/types";
import { toastManager } from "./ui/toast";
import { formatMoney } from "./analyticsFormat";
import { DraftInput } from "./DraftInput";
import { SCOUT_QUERY_KEY, isChecking, trackRecordLine, verdictLabel, verdictReason } from "./scoutCheck";
import { SoldCompsCard } from "./SoldCompsCard";
import { CameraIcon, PlusIcon, XIcon } from "lucide-react";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Spinner } from "./ui/spinner";
import { FormError, PageNote, PageSection, Panel } from "./WorkspacePage";
import { SourcingDetails, SourcingItem, SourcingList, SourcingSetting, sourcingReasonClassName, sourcingTitleClassName } from "./sourcingUi";

const MAX_PHOTOS = 8;

interface Props {
  onOpenListing: (convId: string) => void;
}

/**
 * "Is this worth buying?" while standing in the store: a photo or two of the
 * item and its tag, the asking price if there is one, and Studio answers from
 * sold comps. Bought starts a draft from the same photos; nothing is listed.
 */
export function ScoutPanel({ onOpenListing }: Props) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: SCOUT_QUERY_KEY,
    queryFn: api.scout.list,
    refetchInterval: (current) => (isChecking(current.state.data) ? 3000 : false),
  });
  const [openId, setOpenId] = useState<string | null>(null);
  const checks = query.data?.checks ?? [];
  const open = checks.find((check) => check.id === openId) ?? checks[0] ?? null;
  const record = query.data ? trackRecordLine(query.data.track_record) : null;

  const replace = (check: ScoutCheck) => {
    queryClient.setQueryData<ScoutState>(SCOUT_QUERY_KEY, (current) => current && {
      ...current,
      checks: current.checks.some((row) => row.id === check.id)
        ? current.checks.map((row) => (row.id === check.id ? check : row))
        : [check, ...current.checks],
    });
  };

  return (
    <div className="flex flex-col gap-6">
      <NewCheck
        onCreated={(check) => {
          replace(check);
          setOpenId(check.id);
        }}
      />
      {query.isError ? <FormError>{(query.error as Error).message}</FormError> : null}
      {open ? (
        <CheckCard
          key={open.id}
          check={open}
          onChanged={(check) => {
            replace(check);
            void queryClient.invalidateQueries({ queryKey: SCOUT_QUERY_KEY });
          }}
          onOpenListing={onOpenListing}
        />
      ) : null}
      {checks.length > 1 ? (
        <PageSection aria-label="Earlier checks" title="Earlier checks" description={record ?? undefined}>
          <SourcingList>
            {checks.filter((check) => check.id !== open?.id).map((check) => (
              <HistoryRow key={check.id} check={check} onOpen={() => setOpenId(check.id)} />
            ))}
          </SourcingList>
        </PageSection>
      ) : null}
    </div>
  );
}

function NewCheck({ onCreated }: { onCreated: (check: ScoutCheck) => void }) {
  const [photos, setPhotos] = useState<{ file: File; url: string }[]>([]);
  const [asking, setAsking] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const photosRef = useRef(photos);
  useEffect(() => {
    photosRef.current = photos;
  }, [photos]);
  useEffect(() => () => photosRef.current.forEach((photo) => URL.revokeObjectURL(photo.url)), []);
  const clear = () => {
    photos.forEach((photo) => URL.revokeObjectURL(photo.url));
    setPhotos([]);
  };
  const create = useMutation({
    mutationFn: () => api.scout.create(photos.map((photo) => photo.file), asking.trim() ? Number(asking) : null),
    onSuccess: (check) => {
      clear();
      setAsking("");
      onCreated(check);
    },
    onError: (error: Error) => toastManager.add({ type: "error", title: "Could not start the check", description: error.message }),
  });

  return (
    <Panel divided={false} className="p-4">
      <form
        className="flex flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (photos.length && !create.isPending) create.mutate();
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          capture="environment"
          multiple
          hidden
          onChange={(event) => {
            const room = MAX_PHOTOS - photos.length;
            const picked = Array.from(event.target.files ?? []).slice(0, Math.max(0, room));
            setPhotos((current) => [...current, ...picked.map((file) => ({ file, url: URL.createObjectURL(file) }))]);
            event.target.value = "";
          }}
        />
        <div className="flex flex-wrap gap-2">
          {photos.map((photo, index) => (
            <button
              key={photo.url}
              type="button"
              className="group/thumb relative size-20 cursor-pointer overflow-hidden rounded-lg border border-border/60 outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label={`Remove photo ${index + 1}`}
              onClick={() => {
                URL.revokeObjectURL(photo.url);
                setPhotos((current) => current.filter((row) => row !== photo));
              }}
            >
              <img src={photo.url} alt="" className="size-full object-cover" />
              <span className="absolute inset-0 flex items-center justify-center bg-black/50 text-white opacity-0 transition-opacity group-hover/thumb:opacity-100">
                <XIcon aria-hidden className="size-4" />
              </span>
            </button>
          ))}
          {photos.length < MAX_PHOTOS ? (
            <button
              type="button"
              className="flex size-20 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-border text-xs text-muted-foreground outline-none transition-colors hover:bg-accent/60 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              onClick={() => inputRef.current?.click()}
            >
              {photos.length ? <PlusIcon aria-hidden className="size-4" /> : <CameraIcon aria-hidden className="size-4" />}
              {photos.length ? "Add another" : "Take photos"}
            </button>
          ) : null}
        </div>
        <PageNote>The whole item and its brand tag are usually enough. Tap a photo to remove it.</PageNote>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <SourcingSetting label="Asking (optional)">
            <span className="relative inline-flex items-center">
              <span className="pointer-events-none absolute start-2.5 z-10 text-muted-foreground">$</span>
              <Input
                nativeInput
                size="sm"
                className="w-24 [&_input]:ps-5 [&_input]:tabular-nums"
                inputMode="decimal"
                aria-label="Asking price"
                value={asking}
                onChange={(event) => setAsking(event.target.value.replace(/[^\d.]/g, ""))}
              />
            </span>
          </SourcingSetting>
          <Button type="submit" disabled={!photos.length || create.isPending}>
            {create.isPending ? <><Spinner size="sm" aria-hidden role={undefined} />Uploading…</> : "Is it worth it?"}
          </Button>
        </div>
      </form>
    </Panel>
  );
}

function CheckCard({
  check,
  onChanged,
  onOpenListing,
}: {
  check: ScoutCheck;
  onChanged: (check: ScoutCheck) => void;
  onOpenListing: (convId: string) => void;
}) {
  const onError = (error: Error) => toastManager.add({ type: "error", title: "Could not save", description: error.message });
  const price = useMutation({
    mutationFn: (value: number | null) => api.scout.setAskingPrice(check.id, value),
    onSuccess: onChanged,
    onError,
  });
  const decide = useMutation({
    mutationFn: (decision: "bought" | "passed") => api.scout.decide(check.id, decision),
    onSuccess: (updated) => {
      onChanged(updated);
      if (updated.decision === "bought") {
        toastManager.add({ type: "success", title: "Draft started", description: "Its photos and analysis are ready to generate." });
      }
    },
    onError,
  });
  const label = verdictLabel(check.verdict);

  return (
    <Panel divided={false} className="flex flex-col gap-3 p-4" aria-label={check.title || "Current check"} role="region">
      <div className="flex gap-2 overflow-x-auto">
        {check.photo_urls.map((url) => <img key={url} src={url} alt="" className="size-24 shrink-0 rounded-lg border border-border/60 object-cover" />)}
      </div>
      <div className="text-base font-medium text-foreground">{check.title || "Looking at your photos…"}</div>
      {check.status === "checking" ? (
        <p className="inline-flex items-center gap-2 text-xs text-muted-foreground" role="status">
          <Spinner size="xs" aria-hidden role={undefined} />
          Identifying it and looking up sold listings. This usually takes a minute or two.
        </p>
      ) : null}
      {check.status === "failed" ? <FormError>{check.error}</FormError> : null}
      {check.status === "done" ? (
        <>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
            {label ? (
              <Badge
                size="lg"
                variant={check.verdict === "buy" ? "success" : check.verdict === "pass" ? "error" : "secondary"}
                className="rounded-full px-2.5"
              >
                {label}
              </Badge>
            ) : null}
            <span className="text-sm text-muted-foreground">{verdictReason(check)}</span>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <SourcingSetting label="Asking">
              <DraftInput
                value={check.asking_price != null ? String(check.asking_price) : ""}
                label="Asking price"
                prefix="$"
                width="5.5rem"
                inputMode="decimal"
                disabled={price.isPending}
                clean={(text) => text.replace(/[^\d.]/g, "")}
                accept={(text) => text === "" || Number.isFinite(Number(text))}
                onCommit={(text) => price.mutate(text === "" ? null : Number(text))}
              />
            </SourcingSetting>
            <span className="ms-auto flex items-center gap-2">
              {check.decision === "bought" && check.conversation_id ? (
                <Button onClick={() => onOpenListing(check.conversation_id!)}>Open the draft</Button>
              ) : (
                <>
                  <Button
                    variant="outline"
                    disabled={decide.isPending || check.decision === "passed"}
                    onClick={() => decide.mutate("passed")}
                  >
                    {check.decision === "passed" ? "Passed" : "Pass"}
                  </Button>
                  <Button disabled={decide.isPending} onClick={() => decide.mutate("bought")}>I bought it</Button>
                </>
              )}
            </span>
          </div>
          {check.comps ? (
            <SourcingDetails summary="Sold listings Anteroom found">
              <SoldCompsCard text={check.comps} />
            </SourcingDetails>
          ) : null}
        </>
      ) : null}
    </Panel>
  );
}

function HistoryRow({ check, onOpen }: { check: ScoutCheck; onOpen: () => void }) {
  const label = check.status === "checking" ? "Checking…" : check.status === "failed" ? "Failed" : verdictLabel(check.verdict);
  const outcome = check.sold_price != null
    ? `sold for ${formatMoney(check.sold_price)}`
    : check.decision === "bought" ? "bought" : check.decision === "passed" ? "passed" : null;
  return (
    <SourcingItem
      main={<>
        <button type="button" className={`${sourcingTitleClassName} cursor-pointer`} onClick={onOpen}>
          {check.title || "Unidentified item"}
        </button>
        <div className={sourcingReasonClassName}>
          {[label, check.asking_price != null ? `asking ${formatMoney(check.asking_price)}` : null, outcome]
            .filter(Boolean).join(" · ")}
        </div>
      </>}
      money={check.estimate != null ? <div>{formatMoney(Math.round(check.estimate))}</div> : null}
    />
  );
}

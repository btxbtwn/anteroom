import { useEffect, useState } from "react";
import type { SupabaseClient } from "@supabase/supabase-js";
import { cloudPhotos, type CloudPhoto } from "../api/cloudPhotos";

function PhotoImage({ photo, api }: { photo: CloudPhoto; api: ReturnType<typeof cloudPhotos> }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    void api.read(photo.id, controller.signal).then(blob => {
      if (controller.signal.aborted) return;
      objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);
    }).catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [api, photo.id]);
  return url ? <img src={url} alt="Listing photo" width={photo.width || undefined} height={photo.height || undefined}/> : <span>{error ? "Photo unavailable" : "Loading photo…"}</span>;
}

export function Photos({ client, draftId, disabled, onBusy }: { client: SupabaseClient; draftId: string; disabled: boolean; onBusy: (busy: boolean) => void }) {
  const [api] = useState(() => cloudPhotos(client, draftId));
  const [photos, setPhotos] = useState<CloudPhoto[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let active = true;
    void api.list().then(rows => { if (active) { setPhotos(rows); setLoaded(true); } }).catch(error => { if (active) setError(error.message); });
    return () => { active = false; };
  }, [api]);
  async function upload(files: File[]) {
    setPending(true); onBusy(true); setError("");
    try {
      for (const file of files) {
        const photo = await api.upload(file);
        setPhotos(previous => [...previous, photo]);
      }
    } catch (error) { setError((error as Error).message); }
    finally { setPending(false); onBusy(false); }
  }
  async function remove(photo: CloudPhoto) {
    if (!window.confirm("Remove this photo from the draft?")) return;
    setPending(true); onBusy(true); setError("");
    try { await api.remove(photo.id); setPhotos(previous => previous.filter(row => row.id !== photo.id)); }
    catch (error) { setError((error as Error).message); }
    finally { setPending(false); onBusy(false); }
  }
  return <div className="photo-section"><div className="list-heading"><h2>Photos</h2><span>{photos.length} / 30</span></div>
    <div className="photo-grid">{photos.map(photo => <figure key={photo.id}><PhotoImage photo={photo} api={api}/><button type="button" disabled={disabled || pending} onClick={() => void remove(photo)}>Remove photo</button></figure>)}</div>
    <label htmlFor="photos">Add photos</label><input id="photos" type="file" accept="image/jpeg,image/png,image/webp" multiple disabled={disabled || pending || !loaded || photos.length >= 30}
      onChange={event => { const files = Array.from(event.target.files || []); event.target.value = ""; if (files.length) void upload(files); }}/>
    <p className="field-help">JPEG, PNG or WebP · up to 10 MB each. Saved privately as JPEG, up to 2400 pixels. Photos save as you add them.</p>
    {pending && <p role="status">Saving photos…</p>}{error && <p role="alert" className="message">{error}</p>}
  </div>;
}

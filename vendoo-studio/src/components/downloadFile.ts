import { studioBlob } from "../api/transport";

/** Fetch an export with the app's session and save it under ``filename``. */
export async function downloadFile(href: string, filename: string): Promise<void> {
  const blob = await studioBlob(href);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

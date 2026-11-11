// Every call to eBay or Etsy has a deadline. A timed-out call throws an error
// that names the call, with `timeout` set so a route can answer 504.

export const CALL_TIMEOUT_MS = 10_000;

export function isTimeout(error) {
  return error?.timeout === true || error?.name === "TimeoutError";
}

export async function fetchWithDeadline(label, url, init = {}, ms = CALL_TIMEOUT_MS) {
  try {
    return await fetch(url, { ...init, signal: AbortSignal.timeout(ms) });
  } catch (error) {
    if (!isTimeout(error)) throw error;
    const timedOut = new Error(`${label} timed out after ${ms / 1000}s`);
    timedOut.timeout = true;
    throw timedOut;
  }
}

export function timeoutResponse(label) {
  return new Response(JSON.stringify({ error: `${label} did not answer in time` }), {
    status: 504,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

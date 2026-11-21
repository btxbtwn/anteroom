// The page a seller lands on after signing in to eBay or Etsy.

function escape(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

export function callbackPage({ ok, title, detail }, status = 200) {
  const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>${escape(title)} · Anteroom</title>
<style>
  :root { color-scheme: light dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: Canvas; color: CanvasText; }
  main { max-width: 26rem; padding: 2rem; text-align: center; }
  .mark { font-size: 2.5rem; line-height: 1; margin-bottom: 1rem; color: ${ok ? "#16a34a" : "#dc2626"}; }
  h1 { font-size: 1.25rem; margin: 0 0 0.5rem; }
  p { margin: 0; opacity: 0.75; line-height: 1.5; }
</style>
</head>
<body>
<main>
  <div class="mark" aria-hidden="true">${ok ? "✓" : "✕"}</div>
  <h1>${escape(title)}</h1>
  <p>${escape(detail)}</p>
</main>
</body>
</html>`;
  return new Response(html, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'",
      "referrer-policy": "no-referrer",
    },
  });
}

export function privacyPage() {
  const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Privacy · Anteroom</title>
<style>
  :root { color-scheme: light dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  body { margin: 0; background: Canvas; color: CanvasText; }
  main { max-width: 40rem; margin: 0 auto; padding: 3rem 1.5rem; line-height: 1.6; }
  h1 { font-size: 1.5rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
  li { margin: 0.25rem 0; }
</style>
</head>
<body>
<main>
<h1>Anteroom relay privacy</h1>
<p>Anteroom helps resellers manage and cross-list inventory across the web (<a href="https://myanteroom.com">myanteroom.com</a>), iPhone, and Mac. This page covers the small relay service at relay.anteroom.workers.dev, which routes eBay and Etsy sign-ins, sale notifications, and public eBay research for Anteroom. See the full <a href="https://myanteroom.com/privacy">Anteroom Privacy Policy</a> and <a href="https://myanteroom.com/terms">Terms of Service</a>.</p>

<h2>What the relay keeps</h2>
<ul>
  <li>An ID for each connected Anteroom installation or hosted seller account, and a hashed copy of its access key.</li>
  <li>The eBay user ID and Etsy shop ID you connect, with the account or shop name, so sale events reach the right account.</li>
  <li>For each sale or listing change: the event type and the order, listing and shop IDs. These are deleted when Anteroom picks them up, and after 30 days at the latest.</li>
  <li>While you sign in to eBay or Etsy: the sign-in tokens, for up to 15 minutes, until Anteroom collects them. After that the tokens are stored only in your Anteroom account (or your Mac's Keychain in local mode).</li>
</ul>

<h2>What it does not keep</h2>
<ul>
  <li>Buyer names, addresses, emails or messages.</li>
  <li>Prices, payments, listing text or photos. Etsy API calls and public eBay research (including image searches) pass through without being saved. App credentials stay inside the relay.</li>
</ul>

<h2>Sharing</h2>
<p>Nothing is sold or shared. The relay runs on Cloudflare Workers, which processes the traffic to deliver it.</p>

<h2>Removing your data</h2>
<p>Disconnecting an account in Anteroom (Settings → Connections) removes its link. Deleting your Anteroom account or removing Anteroom's connection removes the relay ID, key and any waiting events. If eBay tells the relay an account was closed, its link is removed automatically.</p>

<h2>Contact</h2>
<p>Privacy questions: <a href="mailto:privacy@myanteroom.com">privacy@myanteroom.com</a> · Support: <a href="mailto:support@myanteroom.com">support@myanteroom.com</a></p>
</main>
</body>
</html>`;
  return new Response(html, {
    headers: {
      "content-type": "text/html; charset=utf-8",
      "content-security-policy": "default-src 'none'; style-src 'unsafe-inline'",
    },
  });
}

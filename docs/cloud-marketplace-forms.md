# Hosted marketplace form previews

The first supported marketplace is Poshmark. In a cloud draft, choose
**Poshmark preview**, then **Load Poshmark form**. Bridge 0.2.39 or later must
be installed and the current Chrome tab connected. Local-app users must update
Anteroom and use its managed Bridge; a standalone web-test ZIP is incompatible
with local marketplace actions. Sign in to Poshmark in the
same Chrome profile when asked.

The preview uses the draft's current General field values, including unsaved
text. It calls the same native Poshmark builder as the desktop app and preserves
its field order, labels, sections, row widths and choices. Conditional rows use
the desktop editor's visibility rules. Photos are read from the seller's private
cloud draft by the web app; no photo bytes or access tokens go to the extension.
To change the preview, edit General and load again. The preview is read-only:
it neither edits nor creates a Poshmark listing, draft or publication.

## Browser protocol

`poshmark_form` is the sole new browser request. A connected Bridge advertises
`poshmark.form.read`. Older Bridges continue to connect; the app asks for an
update before forwarding any listing fields to a reader they do not support.

The content script and worker validate a small flat General-field contract.
Account IDs, file paths, listing/revision pointers, arbitrary URLs, marketplace
specifics, credentials and mutation flags are not accepted inside the listing.
Only native form properties are returned. Diagnostics are reduced to fixed
error codes; headers, cookies and raw upstream responses never enter the page.
The response contract has a 6 MB limit and a maximum of 100 native fields.

The worker checks the approved connection, original document and observed
account before reading and again before returning results. An internal connection
identifier invalidates reads across disconnect/reconnect, even for the same
account. Slow catalog reads run outside the connection-state queue, so signing
out is immediate and a late result is discarded. No read retries are initiated
by the web transport; the existing catalog reader retains its read-only behavior.

Form reads claim the existing exclusive `nativeOperations` scope. Vendoo,
patches, marketplace actions, delists, promotions and page inspections cannot
start alongside the hosted read. Page inspections now reserve that same scope
through completion, instead of only checking whether it was free at startup.
Claims are released after every catalog request has settled, including failures.
No hosted listing mutation command is exposed by this increment.

## Save draft

The web app's only marketplace write through Chrome is **Save draft** on
Poshmark, Mercari and Depop (Bridge 0.2.40). Unlike the preview, its listing
and photos come from the server, in a command the server signs for one
operation; the page cannot change them. It claims the same per-marketplace scope
as the Mac. See [Save draft from the web app](cloud-extension.md#save-draft-from-the-web-app-bridge-0240).

## Verification

Automated tests cover strict payload contracts, connection/document/account
checks, sign-out during a read, sanitized errors/results, the existing operation
lock, native layout and conditional fields, and capability negotiation. Run the
complete extension and frontend suites, production builds, JS syntax checks and
manifest validation.

Live Chrome check: update/reload Bridge 0.2.39, refresh the hosted app, connect,
and open an existing draft. Load Poshmark preview while signed in. Confirm that
the title/description match General, categories and shipping choices appear,
and changing General then reloading the preview changes its values. If the local
app is running an automation job, the reader should report Chrome is busy.
Loading the preview must not save or publish a Poshmark draft.

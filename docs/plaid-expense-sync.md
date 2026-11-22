# Plaid expense sync

Set `PLAID_CLIENT_ID` and `PLAID_SECRET` in the API and scheduled worker
environments. Set `PLAID_ENV` to `sandbox` (the default) or `production`. Plaid must enable Transactions for your credentials and selected
institutions. Use the same environment and credentials on the API and worker.

For hosted browser OAuth, register the app's HTTPS root URL as a Plaid Link
redirect URI in the Plaid Dashboard and set `PLAID_REDIRECT_URI` to that URL.
Complete the institution sign-in in the same browser tab. The local Mac
workspace uses Plaid Link without this hosted redirect.

Open **Analytics → Expenses** and connect a bank or card. Plaid can take time
to prepare the history. The first sync requests up to 730 days. Anteroom checks
every 15 minutes while history is pending, then hourly while the app or hosted
worker is running. Use **Sync now** for an immediate check.

Use a business account for cleaner reports. The category assigned from Plaid
and the merchant name is an estimate. Review categories and exclude any
personal transactions. Exclusions and seller edits persist through sync.
Use **Reconnect** if your bank asks you to sign in again. Plaid Link updates
the existing connection, and Anteroom continues syncing from the saved cursor.
Disconnecting removes the credential and keeps imported expenses. Connecting
again after a disconnect starts a new Plaid Item and requests history again.
Anteroom recognizes previously seen Plaid transaction IDs. If Plaid assigns new
IDs to overlapping history, review those imports for duplicates.

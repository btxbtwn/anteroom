# Listing attempts and marketplace errors

Anteroom's field checks advise the seller. They do not disable List or refuse a confirmed attempt because a title, price, photo, category, size, shipping selection, or eligibility check appears incomplete or invalid. The marketplace decides whether it accepts those values.

After a rejection, Anteroom records and displays the marketplace's response, with credentials and private transport details removed. The form remains editable. A correction saves a new revision; the next confirmed attempt uses that revision. A safe rejection permits another attempt. An uncertain publication must be checked first to avoid creating a duplicate.

Confirmation, seller ownership, approved photo bytes, and routing to the seller's connected account still apply. Anteroom does not automatically publish while correcting fields.

Regression coverage includes local field warnings reaching the adapter, actual API rejection messages returning through the job service, and correction/retry in desktop and phone browser layouts. Browser tests intercept publication requests and run against the sandbox, which independently refuses real publication. They do not prove a marketplace will accept a particular item.

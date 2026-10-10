# Security review and changes

## Findings

1. **Exposed credentials in source (critical).** The Python source contained a Telegram bot token and a MongoDB connection string with embedded credentials. Both are now read from environment variables, and startup validates required settings. The values are intentionally not reproduced here.
2. **Sensitive runtime data in the repository.** `blast_data (1).json` contained Firebase endpoints, user/account identifiers, and SMS-history records with recipient numbers and message text. The dump was removed from the working tree; it is not read by the application (state is stored in MongoDB).
3. **Unsafe bulk messaging and device discovery.** The application was designed to send repeated SMS messages to a user-supplied number through Firebase-connected devices. In this copy, user/admin/owner send actions are blocked, the legacy orchestrator immediately returns, the low-level SMS helper performs no write, Firebase check/discovery helpers make no requests, and the background scanner is not started.
4. **Missing project hygiene.** There was no `.gitignore` or test suite. A `.gitignore` and a credential-free `.env.example` have been added.

## Changes made

- Replaced committed deployment secrets and hardcoded owner/channel IDs with environment configuration.
- Added validation for `BOT_TOKEN`, `MONGO_URI`, and a positive `MAIN_OWNER_ID`; `LOG_CHANNEL_ID` is optional (`0` disables channel logs).
- Disabled SMS dispatch, Firebase probing/device discovery, and background scanning in this working copy.
- Removed the tracked runtime data dump from the working tree.
- Added setup notes and repository ignore rules.

## Verification performed

- `python -m py_compile bomber_firebase.py` — passed.
- The bot was not started; no Telegram, MongoDB, or Firebase runtime endpoint was contacted during validation.
- The import had no test suite; five standard-library safety regression tests were added. Other runtime behavior remains unverified.

## Required follow-up

The upstream Git history is outside this working-tree patch and may still expose the credentials and data. Revoke the old Telegram token, rotate the MongoDB user password, audit/restrict Firebase rules, review service access logs, and remove sensitive blobs from the remote Git history. Do not merely delete the values in a new commit; history must be purged after rotation.

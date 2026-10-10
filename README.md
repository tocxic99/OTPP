# OTPP — imported and safety-hardened copy

This repository contains a Telegram bot that was built to send bulk SMS through Firebase-connected devices. **SMS delivery and Firebase device probing are intentionally disabled in this imported copy.** The send buttons report that status, the legacy send routine exits without sending, the device-send helper makes no network request, Firebase check/discovery helpers return without probing, and the background scanner is not started.

## Security actions applied

- Removed embedded Telegram and MongoDB credentials from the working source. Runtime settings now come from environment variables; see `.env.example`.
- Removed the checked-in runtime state dump from the working tree because it contained Firebase URLs, account identifiers, and SMS history.
- Added ignores for local secrets, runtime exports, and Python bytecode.

## Important: rotate exposed credentials

The repository's public Git history may still contain the original credentials and runtime data. Treat them as compromised: revoke and replace the Telegram bot token, rotate the MongoDB credential, review Firebase database access rules, and purge sensitive blobs from the remote repository history. Deleting a file in a later commit does not remove it from Git history.

## Validation

`python -m py_compile bomber_firebase.py` checks syntax without starting the bot. The bot was not run: it requires real external credentials and connects to remote services. No Telegram, MongoDB, or Firebase runtime endpoint was contacted during validation. A small standard-library safety test suite was added, but application-level tests are still absent; this review does not claim that every runtime defect has been found or fixed.

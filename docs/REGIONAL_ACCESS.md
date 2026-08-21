# Regional access and compliance

## The rule

If `check-access` reports `BLOCKED`, treat public market data and any platform activity as unavailable from the current location. This project does not bypass geographic restrictions.

An IP address, proxy configuration, or transport error does not establish eligibility. In particular, a TLS/SSL failure only means a connection failed; it is not evidence that access is permitted.

## Compliant options

1. Use the offline demo, replay, evidence, and walk-forward commands with the included example files.
2. Use only data sources and historical datasets you are allowed to access and retain.
3. Review the platform's current eligibility and restricted-jurisdiction terms through official channels.
4. If eligibility is unclear, contact the platform's official support or seek qualified local legal advice before attempting platform activity.

## Not supported by this project

- Changing or concealing network location.
- Instructions for configuring proxies, VPNs, relay hosts, or similar tools to access a blocked platform.
- Wallet, private-key, order, or transaction functionality.

The project remains useful for reproducible offline research even when live public market access is unavailable.

# Environments — historical execution vs current stopped state

| Layer | Historical implementation | Present-day claim |
| --- | --- | --- |
| Python | macOS collector, guarded LaunchAgent; Windows scheduled collector in earlier phase | **Disabled**; no active collector |
| Database | `CologneTransitIntelligence` on Windows VM SQL Server | SQL source/sample remains; latest session 16 Oct 2026 |
| Provider | MDD/TRIAS with user-approved API key and shared 250k/month ceiling | No collection currently authorized |
| Static data | VRS GTFS evidence and source identity scoring | Archived, not continuously refreshed |
| Storage/backup | C: SQL data/log; off-C VMware shared backup destination | New verified post-cleanup backup not shown |

Do not commit or distribute `.env`, Keychain payloads, instance admin identities, hostname-specific paths, raw evidence logs or backup media. Tracked LaunchAgent templates remain safe-by-default. PostgreSQL, SQLite research and other past environments were not the authoritative final database. The current documentation does not expose local system account names or provide live start instructions.

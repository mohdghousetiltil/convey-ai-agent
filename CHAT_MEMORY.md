# Chat Memory

This file stores durable assistant context for future chats.

## Product Context

- App name: Convey Agent
- Primary flow: login -> dashboard -> upload -> analysis workspace
- Design direction: clean, professional Swiss-Modern UI
- Shared header rule: use the simple single-line `Convey Agent` header without screen subtitles

## Dashboard Notes

- The dashboard should show recent runs and feel like the home screen after login
- The dashboard table should eventually blend local run history with database-backed matter history
- Preferred dashboard assistant prompts should stay short and limited to about 4-5 suggestions

## Chat Usage Notes

- Chat memory should store only important durable notes, not corpus or document facts
- Chat answers should focus on review risk, conflicts, verification, and next actions
- Keep suggested prompts practical for conveyancing work rather than generic AI prompts

## Data Notes

- Existing backend schema already includes `matters` and `runs` tables
- Short-term dashboard history can likely use existing run and matter records plus a read API
- Extra columns should only be added if we need richer lifecycle tracking, assignment, or client-facing metadata

## Working Agreement

- Prefer concise UI labels
- Keep navigation fluid between dashboard and analysis
- Preserve professional visual polish over decorative complexity

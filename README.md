# RIFT — Every Screen Holds Part of the Truth

RIFT is a real-time cooperative multiplayer escape mission for 2–6 people. Players join from separate phones or laptops using a four-character room code. Each device receives different private clue packets, so no single player can solve a recovery step alone.

## Why this multiplayer mechanic is different

Most browser party games mirror the same prompt to every device. RIFT deliberately distributes different pieces of the puzzle across the crew. One operator may receive a decoder key, another the sequence, another a transformation rule, and another a checksum. The group has to communicate and submit one shared authorization code before the synchronized timer expires.

## Stack

- Python 3.11+
- Streamlit
- Custom CSS
- Python in-memory authoritative game engine
- Streamlit fragments for ~1-second cross-device synchronization
- Optional QR room join after `PUBLIC_URL` is configured

No player accounts or installs are required.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
streamlit run app.py
```

Open the local URL in two different browsers/private windows, create a room on one, and join with the same room code on the other.

## Tests

```bash
pytest -q
```

## Deploy on Streamlit Community Cloud

1. Push this repository to GitHub.
2. In Streamlit Community Cloud choose **Create app** and select this repository.
3. Set the entry point to `app.py`.
4. Deploy.
5. Copy the generated public app URL.
6. In **App settings → Secrets**, add `PUBLIC_URL = "https://your-app-name.streamlit.app"` to enable QR join links.
7. Reboot the app and test with two real devices.

## Multiplayer state model

The `GameManager` is cached as a single Streamlit resource and is the authoritative source of room state inside the running process. Browsers never decide independently whether a mission succeeded; they submit actions to the manager and render snapshots from the shared state.

The first competition build uses in-memory state intentionally to keep the architecture simple and reliable on one app instance. Rooms reset if the hosting process restarts. A production-scale version can replace the manager's storage with Redis/Postgres while preserving the UI and rules.

## Game flow

`LOBBY → BRIEFING → ACTIVE → RESULT → ... → COMPLETE`

A full run contains five recovery steps:

1. Restore Communications
2. Stabilize Reactor
3. Repair Navigation
4. Seal Security Breach
5. Contain the RIFT

Wrong transmissions reduce station integrity. A timeout also reduces integrity. If integrity reaches zero, the mission fails.

## Reliability notes

- Room codes avoid visually ambiguous characters.
- Duplicate callsigns are rejected within a room.
- Late joins are rejected after the mission starts.
- Refresh/reconnect uses a random per-player URL token; QR/invite links contain only the room code, not that private reconnect token.
- Mission clocks are calculated from server-side UTC timestamps.
- Solutions remain hidden from client snapshots until the step ends.
- Shared-state mutations are protected with a re-entrant lock.
- After the lobby, any active operator can advance the mission so a commander disconnect does not strand the crew.

## Hackathon demo checklist

- Device A creates a room.
- Device B joins using the room code.
- Both devices see the crew roster update.
- Start the mission and confirm each device receives different private clues.
- Submit a Team Comms message and confirm it appears on both screens.
- Enter a wrong code and confirm integrity changes on both screens.
- Enter the correct code and confirm both screens transition to the result state.
- Refresh one device mid-game and verify it returns to the same room/session.
- Complete all five recovery steps and capture the final mission screen.

---

**Tagline:** Every screen holds part of the truth.

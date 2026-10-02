from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Dict, List, Optional, Any
import random
import secrets
import string
import time


ROLE_NAMES = ["DECODER", "NAVIGATOR", "SYSTEMS", "COMMS", "ANALYST", "SECURITY"]
ROOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # avoids O/0/I/1
SYMBOLS = ["▲", "◆", "●", "■", "✦", "⬟"]
CHANNELS = ["CYAN", "AMBER", "VIOLET", "LIME", "WHITE", "ORANGE"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


@dataclass
class Player:
    id: str
    name: str
    joined_at: float
    connected: bool = True
    role: str = "CREW"


@dataclass
class Mission:
    number: int
    title: str
    subtitle: str
    duration_seconds: int
    solution: str
    clues_by_player: Dict[str, List[str]]
    operator_player_id: str
    started_at: datetime
    ends_at: datetime
    template: str
    seed: int
    status: str = "ACTIVE"  # ACTIVE | SUCCESS | FAILED
    attempts: int = 0
    completed_by: Optional[str] = None
    completed_at: Optional[datetime] = None
    points_awarded: int = 0
    failure_reason: Optional[str] = None


@dataclass
class ChatMessage:
    player_id: str
    player_name: str
    text: str
    created_at: datetime


@dataclass
class Room:
    code: str
    host_player_id: str
    created_at: datetime
    players: Dict[str, Player] = field(default_factory=dict)
    state: str = "LOBBY"  # LOBBY BRIEFING | ACTIVE | RESULT | COMPLETE
    mission_index: int = -1
    mission: Optional[Mission] = None
    score: int = 0
    integrity: int = 100
    chat: List[ChatMessage] = field(default_factory=list)
    mission_history: List[Dict[str, Any]] = field(default_factory=list)
    team_name: str = "ORION CREW"


class GameError(Exception):
    pass


class GameManager:
    """Thread-safe, in-memory authoritative game state for one Streamlit process.

    This intentionally keeps infrastructure simple for the hackathon build. A production
    deployment can swap the storage layer for Redis/Postgres without changing UI rules.
    """

    def __init__(self):
        self._rooms: Dict[str, Room] = {}
        self._lock = RLock()

    # -------------------------------- Room / player lifecycle -----------------------------
    def create_room(self, player_id: str, player_name: str) -> str:
        player_name = self._clean_name(player_name)
        with self._lock:
            code = self._new_room_code()
            room = Room(code=code, host_player_id=player_id, created_at=utc_now())
            room.players[player_id] = Player(
                id=player_id,
                name=player_name,
                joined_at=time.time(),
                role="COMMANDER",
            )
            self._rooms[code] = room
            return code

    def join_room(self, code: str, player_id: str, player_name: str) -> None:
        code = self._normalize_code(code)
        player_name = self._clean_name(player_name)
        with self._lock:
            room = self._require_room(code)
            self._tick_room(room)
          if room.state != "LOBBY" and player_id not in room.players:
                raise GameError("Mission already in progress. New crew cannot join this run.")
            if player_id in room.players:
                room.players[player_id].connected = True
                room.players[player_id].name = player_name
                return
            if len(room.players) >= 6:
                raise GameError("This mission already has the maximum 6 operators.")
            if any(p.name.casefold() == player_name.casefold() for p in room.players.values()):
                raise GameError("That callsign is already in use in this room.")
            room.players[player_id] = Player(id=player_id, name=player_name, joined_at=time.time())

    def reconnect(self, code: str, player_id: str) -> bool:
        code = self._normalize_code(code)
        with self._lock:
            room = self._rooms.get(code)
            if not room or player_id not in room.players:
                return False
            room.players[player_id].connected = True
            return True

    def leave_room(self, code: str, player_id: str) -> None:
        code = self._normalize_code(code)
        with self._lock:
            room = self._rooms.get(code)
            if not room or player_id not in room.players:
                return
            # During a mission we mark the player disconnected to preserve clue ownership.
            if room.state != "LOBBY":
                room.players[player_id].connected = False
                return
            del room.players[player_id]
            if not room.players:
                self._rooms.pop(code, None)
                return
            if room.host_player_id == player_id:
                room.host_player_id = self._ordered_players(room)[0].id
                room.players[room.host_player_id].role = "COMMANDER"

    # ----------------------------------- Gameplay -------------------------------------
    def start_game(self, code: str, player_id: str) -> None:
        code = self._normalize_code(code)
        with self._lock:
            room = self._require_room(code)
            if player_id != room.host_player_id:
                raise GameError("Only the commander can begin the mission.")
            if len(room.players) < 2:
                raise GameError("At least 2 operators are required.")
            if room.state != "LOBBY":
                raise GameError("The mission has already started.")
            room.state = "BRIEFING"
            room.score = 0
            room.integrity = 100
            room.mission_index = -1
            room.mission = None
            room.mission_history = []
            room.chat = []
            self._assign_roles(room, mission_number=0)

    def launch_next_mission(self, code: str, player_id: str) -> None:
        code = self._normalize_code(code)
        with self._lock:
            room = self._require_room(code)
            if player_id not in room.players:
                raise GameError("Unknown operator.")
            self._tick_room(room)
            if room.state not in {"BRIEFING", "RESULT"}:
                raise GameError("The crew is not ready to launch another recovery step.")
            next_idx = room.mission_index + 1
            if next_idx >= 5:
                room.state = "COMPLETE"
                room.mission = None
                return
            room.mission_index = next_idx
            mission_number = next_idx + 1
            self._assign_roles(room, mission_number=mission_number)
            room.mission = self._generate_mission(room, mission_number)
            room.state = "ACTIVE"

    def submit_solution(self, code: str, player_id: str, answer: str) -> Dict[str, Any]:
        code = self._normalize_code(code)
        answer = "".join(ch for ch in str(answer) if ch.isalnum()).upper()
        with self._lock:
            room = self._require_room(code)
            self._tick_room(room)
            if room.state != "ACTIVE" or not room.mission or room.mission.status != "ACTIVE":
                raise GameError("This recovery window is no longer accepting commands.")
            if player_id not in room.players:
                raise GameError("Unknown operator.")
            mission = room.mission
            mission.attempts += 1
            if answer == mission.solution:
                remaining = max(0, int((mission.ends_at - utc_now()).total_seconds()))
                speed_bonus = remaining * 8
                accuracy_bonus = max(0, 250 - (mission.attempts - 1) * 75)
                mission.points_awarded = 500 + speed_bonus + accuracy_bonus
                mission.status = "SUCCESS"
                mission.completed_by = player_id
                mission.completed_at = utc_now()
                room.score += mission.points_awarded
                room.integrity = min(100, room.integrity + 3)
                room.state = "RESULT"
                self._archive_current_mission(room)
                return {"correct": True, "points": mission.points_awarded}

            damage = 12 if mission.number < 5 else 16
            room.integrity = max(0, room.integrity - damage)
            if room.integrity <= 0:
                mission.status = "FAILED"
                mission.failure_reason = "Station integrity reached 0%."
                mission.completed_at = utc_now()
                room.state = "COMPLETE"
                self._archive_current_mission(room)
            return {"correct": False, "damage": damage}

    def add_chat(self, code: str, player_id: str, text: str) -> None:
        code = self._normalize_code(code)
        text = " ".join(str(text).strip().split())[:180]
        if not text:
            return
        with self._lock:
            room = self._require_room(code)
            player = room.players.get(player_id)
            if not player:
                raise GameError("Unknown operator.")
            room.chat.append(ChatMessage(player_id, player.name, text, utc_now()))
            room.chat = room.chat[-40:]

    def play_again(self, code: str, player_id: str) -> None:
        code = self._normalize_code(code)
        with self._lock:
            room = self._require_room(code)
            if player_id not in room.players:
                raise GameError("Unknown operator.")
            room.state = "LOBBY"
            room.score = 0
            room.integrity = 100
            room.mission_index = -1
            room.mission = None
            room.mission_history = []
            room.chat = []
            for p in room.players.values():
                p.role = "CREW"
                p.connected = True
            room.players[room.host_player_id].role = "COMMANDER"

    # ----------------------------------- Queries --------------------------------------
    def room_exists(self, code: str) -> bool:
        with self._lock:
            return self._normalize_code(code) in self._rooms

    def get_snapshot(self, code: str, viewer_player_id: Optional[str] = None) -> Dict[str, Any]:
        code = self._normalize_code(code)
        with self._lock:
            room = self._require_room(code)
            self._tick_room(room)
            ordered = self._ordered_players(room)
            mission = room.mission
            snapshot: Dict[str, Any] = {
                "code": room.code,
                "host_player_id": room.host_player_id,
                "state": room.state,
                "mission_index": room.mission_index,
                "score": room.score,
                "integrity": room.integrity,
                "team_name": room.team_name,
                "players": [asdict(p) for p in ordered],
                "chat": [
                    {
                        "player_id": m.player_id,
                        "player_name": m.player_name,
                        "text": m.text,
                        "created_at": iso(m.created_at),
                    }
                    for m in room.chat[-16:]
                ],
                "mission_history": list(room.mission_history),
            }
            if mission:
                viewer_clues = mission.clues_by_player.get(viewer_player_id or "", [])
                snapshot["mission"] = {
                    "number": mission.number,
                    "title": mission.title,
                    "subtitle": mission.subtitle,
                    "duration_seconds": mission.duration_seconds,
                    "status": mission.status,
                    "attempts": mission.attempts,
                    "operator_player_id": mission.operator_player_id,
                    "started_at": iso(mission.started_at),
                    "ends_at": iso(mission.ends_at),
                    "remaining_seconds": max(0, int((mission.ends_at - utc_now()).total_seconds())),
                    "points_awarded": mission.points_awarded,
                    "failure_reason": mission.failure_reason,
                    "viewer_clues": viewer_clues,
                    "template": mission.template,
                    # solution appears only after result/failure
                    "solution": mission.solution if mission.status != "ACTIVE" else None,
                }
            else:
                snapshot["mission"] = None
            return snapshot

    # --------------------------------- Internal logic ---------------------------------
    def _tick_room(self, room: Room) -> None:
        if room.state == "ACTIVE" and room.mission and room.mission.status == "ACTIVE":
            if utc_now() >= room.mission.ends_at:
                room.mission.status = "FAILED"
                room.mission.failure_reason = "Recovery window expired."
                room.mission.completed_at = utc_now()
                room.integrity = max(0, room.integrity - 15)
                room.state = "RESULT" if room.integrity > 0 else "COMPLETE"
                self._archive_current_mission(room)

    def _archive_current_mission(self, room: Room) -> None:
        m = room.mission
        if not m:
            return
        if any(h.get("number") == m.number for h in room.mission_history):
            return
        room.mission_history.append(
            {
                "number": m.number,
                "title": m.title,
                "status": m.status,
                "attempts": m.attempts,
                "points": m.points_awarded,
                "solution": m.solution,
            }
        )

    def _assign_roles(self, room: Room, mission_number: int) -> None:
        players = self._ordered_players(room)
        for i, player in enumerate(players):
            player.role = ROLE_NAMES[(i + max(0, mission_number - 1)) % len(ROLE_NAMES)]

    def _generate_mission(self, room: Room, mission_number: int) -> Mission:
        seed = secrets.randbelow(10_000_000)
        rng = random.Random(seed)
        players = self._ordered_players(room)
        operator = players[(mission_number - 1) % len(players)]
        now = utc_now()
        duration = [95, 100, 105, 110, 125][mission_number - 1]

        if mission_number == 1:
            title, subtitle, template = "Restore Communications", "Decode the emergency uplink", "SYMBOL_CIPHER"
            solution, packets = self._puzzle_symbol_cipher(rng, reverse=False)
        elif mission_number == 2:
            title, subtitle, template = "Stabilize Reactor", "Align the coolant channels", "CHANNEL_LOCK"
            solution, packets = self._puzzle_channel_lock(rng)
        elif mission_number == 3:
            title, subtitle, template = "Repair Navigation", "Reconstruct the orbital route", "ORBIT_ROUTE"
            solution, packets = self._puzzle_symbol_cipher(rng, reverse=True)
        elif mission_number == 4:
            title, subtitle, template = "Seal Security Breach", "Authenticate the station core", "SECURITY_MATRIX"
            solution, packets = self._puzzle_security_matrix(rng)
        else:
            title, subtitle, template = "Contain the RIFT", "Final synchronized override", "RIFT_OVERRIDE"
            solution, packets = self._puzzle_final(rng)

        clues_by_player = {p.id: [] for p in players}
        # Round-robin clue packets; for 2 players each receives multiple complementary clues.
        for idx, packet in enumerate(packets):
            clues_by_player[players[idx % len(players)].id].append(packet)

        # Every player gets role context. Operator gets a unique instruction, but everyone can submit
        # in the UI as a resiliency fallback.
        for p in players:
            clues_by_player[p.id].insert(0, f"ROLE // {p.role}")
        clues_by_player[operator.id].append("PRIMARY CONTROL // You are the designated command operator for this recovery step.")

        return Mission(
            number=mission_number,
            title=title,
            subtitle=subtitle,
            duration_seconds=duration,
            solution=solution,
            clues_by_player=clues_by_player,
            operator_player_id=operator.id,
            started_at=now,
            ends_at=now + timedelta(seconds=duration),
            template=template,
            seed=seed,
        )

    def _puzzle_symbol_cipher(self, rng: random.Random, reverse: bool) -> tuple[str, List[str]]:
        symbols = rng.sample(SYMBOLS, 4)
        digits = rng.sample(list("123456789"), 4)
        mapping = dict(zip(symbols, digits))
        sequence = rng.sample(symbols, 4)
        ordered = list(reversed(sequence)) if reverse else sequence
        solution = "".join(mapping[s] for s in ordered)
        checksum = sum(int(x) for x in solution)
        packets = [
            "DECODER KEY // " + "   ".join(f"{s}={mapping[s]}" for s in symbols),
            "NAV ROUTE // " + " → ".join(sequence),
            "COMMS ORDER // " + ("READ THE NAV ROUTE IN REVERSE." if reverse else "READ THE NAV ROUTE LEFT TO RIGHT."),
            f"ANALYST CHECK // The four authorization digits sum to {checksum}.",
            f"SECURITY CHECK // First digit is {'EVEN' if int(solution[0]) % 2 == 0 else 'ODD'}; last digit is {'EVEN' if int(solution[-1]) % 2 == 0 else 'ODD'}.",
        ]
        return solution, packets

    def _puzzle_channel_lock(self, rng: random.Random) -> tuple[str, List[str]]:
        channels = rng.sample(CHANNELS, 4)
        values = rng.sample(range(1, 10), 4)
        table = dict(zip(channels, values))
        order = rng.sample(channels, 4)
        offset = rng.choice([1, 2, 3])
        adjusted = [((table[c] + offset - 1) % 9) + 1 for c in order]
        solution = "".join(str(v) for v in adjusted)
        packets = [
            "COOLANT READINGS // " + "   ".join(f"{c}:{table[c]}" for c in channels),
            "FLOW ORDER // " + " → ".join(order),
            f"REACTOR RULE // Add {offset} to EACH channel value. Values wrap after 9 back to 1.",
            f"THERMAL CHECK // Final code digit total = {sum(adjusted)}.",
            f"SAFETY CHECK // Code starts with {solution[0]} and ends with {solution[-1]}.",
        ]
        return solution, packets

    def _puzzle_security_matrix(self, rng: random.Random) -> tuple[str, List[str]]:
        labels = ["A", "B", "C", "D"]
        base = rng.sample(range(1, 10), 4)
        table = dict(zip(labels, base))
        order = rng.sample(labels, 4)
        mode = rng.choice(["PLUS2", "DOUBLE_WRAP"])
        if mode == "PLUS2":
            transformed = [((table[x] + 2 - 1) % 9) + 1 for x in order]
            rule_text = "Add 2 to every selected value; wrap 10→1 and 11→2."
        else:
            transformed = [((table[x] * 2 - 1) % 9) + 1 for x in order]
            rule_text = "Double every selected value; wrap values above 9 by cycling 1–9."
        solution = "".join(str(v) for v in transformed)
        packets = [
            "ACCESS MATRIX // " + "   ".join(f"{k}={table[k]}" for k in labels),
            "AUTH PATH // " + " → ".join(order),
            "CORE RULE // " + rule_text,
            f"AUDIT CHECK // Final digit total = {sum(transformed)}.",
            f"SECURITY CHECK // Middle pair is {solution[1:3]}.",
        ]
        return solution, packets

    def _puzzle_final(self, rng: random.Random) -> tuple[str, List[str]]:
        symbols = rng.sample(SYMBOLS, 5)
        digits = rng.sample(list("123456789"), 5)
        mapping = dict(zip(symbols, digits))
        route = rng.sample(symbols, 5)
        direction = rng.choice(["FORWARD", "REVERSE"])
        seq = route if direction == "FORWARD" else list(reversed(route))
        shift = rng.choice([1, 2])
        values = [((int(mapping[s]) + shift - 1) % 9) + 1 for s in seq]
        solution = "".join(str(v) for v in values)
        packets = [
            "RIFT KEY // " + "   ".join(f"{s}={mapping[s]}" for s in symbols),
            "RIFT VECTOR // " + " → ".join(route),
            f"DIRECTION // Read vector {direction}.",
            f"PHASE SHIFT // Add {shift} to every decoded digit; wrap after 9 back to 1.",
            f"FINAL CHECK // Five digits; total = {sum(values)}; final digit = {solution[-1]}.",
            "OVERRIDE // Coordinate verbally. One bad transmission damages station integrity.",
        ]
        return solution, packets

    def _ordered_players(self, room: Room) -> List[Player]:
        return sorted(room.players.values(), key=lambda p: (p.joined_at, p.name.casefold()))

    def _new_room_code(self) -> str:
        for _ in range(100):
            code = "".join(secrets.choice(ROOM_ALPHABET) for _ in range(4))
            if code not in self._rooms:
                return code
        raise GameError("Could not allocate a room code. Please try again.")

    def _require_room(self, code: str) -> Room:
        room = self._rooms.get(code)
        if not room:
            raise GameError("Mission room not found. Check the four-character code.")
        return room

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = " ".join(str(name).strip().split())[:20]
        allowed = "".join(ch for ch in cleaned if ch.isalnum() or ch in " _-.")
        if len(allowed) < 2:
            raise GameError("Enter a callsign with at least 2 characters.")
        return allowed

    @staticmethod
    def _normalize_code(code: str) -> str:
        return "".join(ch for ch in str(code).upper().strip() if ch in ROOM_ALPHABET)[:4]

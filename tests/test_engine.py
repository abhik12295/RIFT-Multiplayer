from game_engine import GameError, GameManager


def make_room(player_count=2):
    gm = GameManager()
    code = gm.create_room("p1", "Nova")
    for i in range(2, player_count + 1):
        gm.join_room(code, f"p{i}", f"Crew{i}")
    return gm, code


def test_room_create_and_join():
    gm, code = make_room(3)
    snap = gm.get_snapshot(code, "p1")
    assert len(code) == 4
    assert len(snap["players"]) == 3
    assert snap["state"] == "LOBBY"


def test_requires_two_players_to_start():
    gm = GameManager()
    code = gm.create_room("p1", "Nova")
    try:
        gm.start_game(code, "p1")
        assert False, "expected GameError"
    except GameError:
        pass


def test_start_and_launch_mission():
    gm, code = make_room(2)
    gm.start_game(code, "p1")
    assert gm.get_snapshot(code, "p1")["state"] == "BRIEFING"
    gm.launch_next_mission(code, "p1")
    snap1 = gm.get_snapshot(code, "p1")
    snap2 = gm.get_snapshot(code, "p2")
    assert snap1["state"] == "ACTIVE"
    assert snap1["mission"]["number"] == 1
    assert snap1["mission"]["viewer_clues"] != snap2["mission"]["viewer_clues"]
    assert snap1["mission"]["solution"] is None


def test_wrong_then_correct_solution():
    gm, code = make_room(2)
    gm.start_game(code, "p1")
    gm.launch_next_mission(code, "p1")
    room = gm._rooms[code]  # test authoritative state directly
    solution = room.mission.solution
    result = gm.submit_solution(code, "p2", "0000")
    assert result["correct"] is False
    assert gm.get_snapshot(code, "p1")["integrity"] == 88
    result = gm.submit_solution(code, "p1", solution)
    assert result["correct"] is True
    snap = gm.get_snapshot(code, "p1")
    assert snap["state"] == "RESULT"
    assert snap["mission"]["solution"] == solution
    assert snap["score"] > 0


def test_duplicate_name_rejected():
    gm = GameManager()
    code = gm.create_room("p1", "Nova")
    try:
        gm.join_room(code, "p2", "nova")
        assert False, "expected duplicate-name rejection"
    except GameError:
        pass

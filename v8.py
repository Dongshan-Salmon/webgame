from flask import Flask, request, jsonify, render_template
import random
import threading
import os
import time
import uuid

# 初始化 Flask App，它會自動從 'templates' 資料夾尋找網頁
app = Flask(__name__)

# --- 全域狀態 (多房間) ---
rooms = {}
rooms_lock = threading.Lock()

# --- 遊戲設定 ---
TIMEOUT_SECONDS = 10  # 玩家超時時間 (秒)
LOBBY_KICK_TIMEOUT = 60 # 大廳玩家離線踢除時間 (秒)
REAP_INTERVAL = 5     # 巡邏員檢查間隔 (秒)

ALL_ROLES = {
    "good": ["梅林", "派西維爾",
             "亞瑟的忠臣", "亞瑟的忠臣", "亞瑟的忠臣", "亞瑟的忠臣", "亞瑟的忠臣", "亞瑟的忠臣"],
    "evil": ["莫甘娜", "刺客", "莫德雷德", "奧伯倫",
             "莫德雷德的爪牙", "莫德雷德的爪牙", "莫德雷德的爪牙", "莫德雷德的爪牙"]
}

ROLE_CONFIG = {
    5: (["梅林", "派西維爾", "亞瑟的忠臣"], ["莫甘娜", "刺客"]),
    6: (["梅林", "派西維爾", "亞瑟的忠臣", "亞瑟的忠臣"], ["莫甘娜", "刺客"]),
    7: (["梅林", "派西維爾", "亞瑟的忠臣", "亞瑟的忠臣"], ["莫甘娜", "刺客", "奧伯倫"]),
    8: (["梅林", "派西維爾", "亞瑟的忠臣", "亞瑟的忠臣", "亞瑟的忠臣"], ["莫甘娜", "刺客", "莫德雷德"]),
    9: (["梅林", "派西維爾", "亞瑟的忠臣", "亞瑟的忠臣", "亞瑟的忠臣", "亞瑟的忠臣"], ["莫甘娜", "刺客", "莫德雷德"]),
    10: (["梅林", "派西維爾", "亞瑟的忠臣", "亞瑟的忠臣", "亞瑟的忠臣", "亞瑟的忠臣"], ["莫甘娜", "刺客", "莫德雷德", "奧伯倫"]),
}
MISSION_SIZES = {
    5: [2, 3, 2, 3, 3], 6: [2, 3, 4, 3, 4], 7: [2, 3, 3, 4, 4],
    8: [3, 4, 4, 5, 5], 9: [3, 4, 4, 5, 5], 10: [3, 4, 4, 5, 5],
}
TWO_FAILS_MISSION_REQUIRED = { 7: 4, 8: 4, 9: 4, 10: 4 }

# --- 輔助函式 ---
def get_new_room_code():
    return ''.join(random.choices('ABCDEFGHIJKLMNPQRSTUVWXYZ123456789', k=5))

def find_player_by_token(room, token):
    if not room: return None
    for p in room.get('players', []):
        if p.get('token') == token:
            return p
    return None

def find_player_by_name(room, name):
    if not room: return None
    for p in room.get('players', []):
        if p.get('name') == name:
            return p
    return None

# --- API 端點 (Routes) ---
@app.route('/')
def home():
    return render_template('index.html')

@app.errorhandler(404)
def page_not_found(e):
    return "404 - Page not found.", 404

@app.route('/create_room', methods=['POST'])
def create_room():
    data = request.json
    player_name = data.get('playerName')
    with rooms_lock:
        room_code = get_new_room_code()
        while room_code in rooms: room_code = get_new_room_code()

        token = str(uuid.uuid4())
        player = {
            "name": player_name, "isHost": True, "isReady": True,
            "token": token, "last_seen": time.time(), "status": "connected"
        }

        default_max_players = 5
        default_good, default_evil = ROLE_CONFIG[default_max_players]
        default_mission_track = MISSION_SIZES[default_max_players]

        rooms[room_code] = {
            "players": [player],
            "lobbyPlayerOrder": [player_name],
            "settings": {
                "maxPlayers": default_max_players, "password": "", "useLady": True,
                "randomizeOrder": True, "customRoles": default_good + default_evil,
                "missionTrack": default_mission_track
            },
            "gameState": None,
            "created_at": time.time()
        }
    return jsonify({"success": True, "roomCode": room_code, "token": token})

@app.route('/join_room', methods=['POST'])
def join_room():
    data = request.json
    player_name, room_code = data.get('playerName'), data.get('roomCode')
    with rooms_lock:
        if not room_code:
            available_rooms = [rc for rc, r in rooms.items() if not r['settings']['password'] and not r['gameState'] and len(r['players']) < r['settings']['maxPlayers']]
            if not available_rooms: return jsonify({"success": False, "message": "沒有可加入的公開房間"}), 404
            room_code = random.choice(available_rooms)

        if room_code not in rooms: return jsonify({"success": False, "message": "房間不存在"}), 404
        room = rooms[room_code]
        if len(room['players']) >= room['settings']['maxPlayers']: return jsonify({"success": False, "message": "房間已滿"}), 403
        if room['gameState']: return jsonify({"success": False, "message": "遊戲已開始"}), 403
        if any(p['name'] == player_name for p in room['players']): return jsonify({"success": False, "message": "此名稱已被使用"}), 409

        token = str(uuid.uuid4())
        player = {
            "name": player_name, "isHost": False, "isReady": False,
            "token": token, "last_seen": time.time(), "status": "connected"
        }
        room['players'].append(player)
        room['lobbyPlayerOrder'] = [p['name'] for p in room['players']]
    return jsonify({"success": True, "roomCode": room_code, "token": token})

@app.route('/reconnect', methods=['POST'])
def reconnect():
    data = request.json
    room_code, token = data.get('roomCode'), data.get('token')
    with rooms_lock:
        if room_code not in rooms: return jsonify({"success": False, "message": "房間不存在"}), 404
        room = rooms[room_code]
        player = find_player_by_token(room, token)
        if not player: return jsonify({"success": False, "message": "玩家身份驗證失敗"}), 403

        player['status'] = 'connected'
        player['last_seen'] = time.time()
        return room_state_logic(room_code, player['name'])

@app.route('/room_state', methods=['POST'])
def room_state():
    data = request.json
    room_code, token = data.get('roomCode'), data.get('token')
    with rooms_lock:
        if room_code not in rooms: return jsonify({"success": False, "message": "房間不存在"}), 404
        room = rooms[room_code]
        player = find_player_by_token(room, token)
        if not player: return jsonify({"success": False, "message": "玩家身份驗證失敗"}), 403

        player['last_seen'] = time.time()
        player['status'] = 'connected'
        return room_state_logic(room_code, player['name'])

def room_state_logic(room_code, player_name):
    room = rooms[room_code]
    response_data = room.copy()

    if room.get('gameState'):
        gs = room['gameState']
        my_role_data = gs['assigned_roles'].get(player_name, {})
        evil_roles = [r for r in gs['roles_in_game'] if r in ALL_ROLES['evil']]
        my_info = {
            "role": my_role_data.get("role"), "is_evil": my_role_data.get("role") in evil_roles,
            "role_info": "", "known_evil": []
        }
        if my_info["role"] == "梅林": my_info["role_info"] = f"你知道的壞人是: {', '.join([p for p, d in gs['assigned_roles'].items() if d['role'] in evil_roles and d['role'] != '莫德雷德'])}"
        elif my_info["role"] == "派西維爾": my_info["role_info"] = f"梅林和莫甘娜是: {', '.join([p for p, d in gs['assigned_roles'].items() if d['role'] in ['梅林', '莫甘娜']])}"
        elif my_info["is_evil"] and my_info["role"] != "奧伯倫":
            known_evil = [p for p, d in gs['assigned_roles'].items() if d['role'] in evil_roles and d['role'] != '奧伯倫']
            my_info["known_evil"] = known_evil
            my_info["role_info"] = f"你的邪惡夥伴是: {', '.join(p for p in known_evil if p != player_name)}"
        my_info["last_lady_reveal"] = my_role_data.pop("last_lady_reveal", None)

        response_data['gameState'] = {
            "phase": gs["phase"], "phase_text": gs["phase_text"], "player_order": gs["player_order"],
            "current_leader": gs["player_order"][gs["current_leader_index"]],
            "mission_number": gs["mission_number"], "quest_track": gs["quest_track"],
            "my_info": my_info,
            "is_leader": gs["player_order"][gs["current_leader_index"]] == player_name,
            "team_proposal": gs["team_proposal"],
            "votes": gs["votes"], "mission_votes": gs["mission_votes"],
            "my_vote": gs["votes"].get(player_name),
            "my_mission_vote": gs["mission_votes"].get(player_name),
            "is_on_mission": player_name in gs["team_proposal"],
            "vote_reject_count": gs["vote_reject_count"],
            "mission_team_sizes": gs["mission_team_sizes"],
            "mission_team_size": gs["mission_team_sizes"][gs['mission_number'] - 1],
            "lady_holder": gs.get("lady_holder"),
            "is_lady_holder": gs.get("lady_holder") == player_name,
            "lady_used_on": gs.get("lady_used_on", []),
            "game_start_time": gs["game_start_time"],
            "last_vote_details": gs.get("last_vote_details"),
            "mission_history": gs.get("mission_history", []),
            "all_possible_roles": {
                "good": [r for r in gs['roles_in_game'] if r in ALL_ROLES['good']],
                "evil": [r for r in gs['roles_in_game'] if r in ALL_ROLES['evil']]
            }
        }
        if gs["phase"] == "end": response_data["gameState"]["game_over_data"] = gs["game_over_data"]
    else:
        good_roles = [r for r in room['settings']['customRoles'] if r in ALL_ROLES['good']]
        evil_roles = [r for r in room['settings']['customRoles'] if r in ALL_ROLES['evil']]
        
        response_data['all_roles_pool'] = ALL_ROLES

        response_data['gameInfo'] = {
            "good_count": len(good_roles), "evil_count": len(evil_roles),
            "roles": good_roles + evil_roles, "mission_map": room['settings']['missionTrack']
        }
    return jsonify(response_data)

@app.route('/toggle_ready', methods=['POST'])
def toggle_ready():
    data = request.json
    with rooms_lock:
        room = rooms.get(data['roomCode'])
        player = find_player_by_token(room, data['token'])
        if not room or not player: return jsonify({}), 404
        if not player['isHost']: player['isReady'] = not player['isReady']
    return jsonify({"success": True})

@app.route('/update_settings', methods=['POST'])
def update_settings():
    data = request.json
    with rooms_lock:
        room = rooms.get(data['roomCode'])
        player = find_player_by_token(room, data['token'])
        if not room or not player or not player['isHost']: return jsonify({"success": False, "message": "非房主無權操作"}), 403
        if room['gameState']: return jsonify({"success": False, "message": "遊戲進行中無法修改設定"}), 403

        settings = data['settings']
        new_max_players = int(settings.get('maxPlayers', room['settings']['maxPlayers']))

        max_players_changed = room['settings']['maxPlayers'] != new_max_players
        
        if max_players_changed:
            room['settings']['maxPlayers'] = new_max_players
            if new_max_players in ROLE_CONFIG:
                good, evil = ROLE_CONFIG[new_max_players]
                room['settings']['customRoles'] = good + evil
                room['settings']['missionTrack'] = MISSION_SIZES[new_max_players]
        
        if 'customRoles' in settings and not max_players_changed:
            room['settings']['customRoles'] = settings['customRoles']

        if 'password' in settings: room['settings']['password'] = settings['password']
        if 'useLady' in settings: room['settings']['useLady'] = settings['useLady']
        if 'randomizeOrder' in settings: room['settings']['randomizeOrder'] = settings['randomizeOrder']
            
    return jsonify({"success": True})

@app.route('/update_mission_track', methods=['POST'])
def update_mission_track():
    data = request.json
    with rooms_lock:
        room = rooms.get(data['roomCode'])
        player = find_player_by_token(room, data['token'])
        if not room or not player or not player['isHost']: return jsonify({"success": False, "message": "非房主無權操作"}), 403
        if room['gameState']: return jsonify({"success": False, "message": "遊戲進行中無法修改設定"}), 403
        new_track = data.get('missionTrack', [])
        if len(new_track) == 5 and all(isinstance(x, int) and 1 <= x <= room['settings']['maxPlayers'] for x in new_track):
            room['settings']['missionTrack'] = new_track
        else:
            return jsonify({"success": False, "message": "無效的任務軌跡設定"}), 400
    return jsonify({"success": True})

@app.route('/leave_room', methods=['POST'])
def leave_room():
    data = request.json
    with rooms_lock:
        room_code = data.get('roomCode')
        if room_code not in rooms: return jsonify({"success": True})
        room = rooms[room_code]
        player_to_remove = find_player_by_token(room, data['token'])
        if not player_to_remove: return jsonify({"success": True})

        was_host = player_to_remove['isHost']
        room['players'] = [p for p in room['players'] if p['token'] != data['token']]
        room['lobbyPlayerOrder'] = [p['name'] for p in room['players']]
        if not room['players']:
            if room_code in rooms: del rooms[room_code]
        elif was_host:
            room['players'][0]['isHost'] = True
            room['players'][0]['isReady'] = True
    return jsonify({"success": True})

@app.route('/kick_player', methods=['POST'])
def kick_player():
    data = request.json
    with rooms_lock:
        room = rooms.get(data['roomCode'])
        player = find_player_by_token(room, data['token'])
        if not room or not player or not player['isHost']: return jsonify({"success": False, "message": "非房主無權操作"}), 403
        if room['gameState']: return jsonify({"success": False, "message": "遊戲進行中無法踢人"}), 403

        target_name = data.get('targetName')
        room['players'] = [p for p in room['players'] if p['name'] != target_name]
        room['lobbyPlayerOrder'] = [p for p in room['lobbyPlayerOrder'] if p != target_name]
    return jsonify({"success": True})

@app.route('/transfer_host', methods=['POST'])
def transfer_host():
    data = request.json
    with rooms_lock:
        room = rooms.get(data['roomCode'])
        player = find_player_by_token(room, data['token'])
        if not room or not player or not player['isHost']: return jsonify({"success": False, "message": "非房主無權操作"}), 403
        if room['gameState']: return jsonify({"success": False, "message": "遊戲進行中無法轉移房主"}), 403

        target_name = data.get('targetName')
        for p in room['players']:
            p['isHost'] = (p['name'] == target_name)
            p['isReady'] = p['isHost']
    return jsonify({"success": True})

@app.route('/update_player_order', methods=['POST'])
def update_player_order():
    data = request.json
    with rooms_lock:
        room = rooms.get(data['roomCode'])
        player = find_player_by_token(room, data['token'])
        if not room or not player or not player['isHost']: return jsonify({"success": False, "message": "非房主無權操作"}), 403
        if room['gameState']: return jsonify({"success": False, "message": "遊戲進行中無法改變順序"}), 403
        room['lobbyPlayerOrder'] = data['newOrder']
    return jsonify({"success": True})

@app.route('/start_game', methods=['POST'])
def start_game():
    data = request.json
    with rooms_lock:
        room_code = data.get('roomCode')
        room = rooms.get(room_code)
        player = find_player_by_token(room, data['token'])
        if not room or not player or not player['isHost']: return jsonify({"success": False, "message": "非房主無權操作"}), 403

        player_count = len(room['players'])
        if player_count < 5: return jsonify({"success": False, "message": "玩家人數不足 5 人"}), 400
        if player_count != room['settings']['maxPlayers']: return jsonify({"success": False, "message": "玩家人數未達房間設定上限"}), 400
        if not all(p['isReady'] for p in room['players']): return jsonify({"success": False, "message": "尚有玩家未準備"}), 400
        if len(room['settings']['customRoles']) != player_count: return jsonify({"success": False, "message": "所選角色數量與玩家人數不符"}), 400

        player_order = room['lobbyPlayerOrder'][:]
        if room['settings']['randomizeOrder']: random.shuffle(player_order)
        roles = room['settings']['customRoles'][:]
        random.shuffle(roles)
        assigned = {name: {"role": role} for name, role in zip(player_order, roles)}

        lady_holder = None
        lady_used_on = []
        if room['settings']['useLady'] and player_count >= 7:
            good_players = [p for p, d in assigned.items() if d['role'] in ALL_ROLES['good']]
            if good_players:
                lady_holder = random.choice(good_players)
                lady_used_on.append(lady_holder)

        room['gameState'] = {
            "player_order": player_order, "assigned_roles": assigned, "roles_in_game": roles,
            "current_leader_index": 0, "mission_number": 1,
            "quest_track": ["pending"] * 5, "mission_history": [],
            "phase": "team_building", "phase_text": "組建隊伍",
            "team_proposal": [], "votes": {}, "mission_votes": {}, "vote_reject_count": 0,
            "game_start_time": time.time() * 1000,
            "lady_holder": lady_holder, "lady_used_missions": [], "lady_used_on": lady_used_on,
            "last_vote_details": None,
            "mission_team_sizes": room['settings']['missionTrack']
        }
    return jsonify({"success": True})

@app.route('/return_to_lobby', methods=['POST'])
def return_to_lobby():
    data = request.json
    with rooms_lock:
        room = rooms.get(data['roomCode'])
        player = find_player_by_token(room, data['token'])
        if not room or not player or not player['isHost']: return jsonify({"success": False, "message": "非房主無權操作"}), 403

        room['gameState'] = None
        room['lobbyPlayerOrder'] = [p['name'] for p in room['players']]
        for p in room['players']: p['isReady'] = p['isHost']
    return jsonify({"success": True})

@app.route('/action', methods=['POST'])
def handle_action():
    data = request.json
    with rooms_lock:
        room_code = data.get('roomCode')
        if room_code not in rooms or not rooms[room_code]['gameState']: return jsonify({"success": False}), 404
        room = rooms[room_code]
        player = find_player_by_token(room, data['token'])
        if not player: return jsonify({"success": False, "message": "玩家身份驗證失敗"}), 403

        process_game_action(room_code, player['name'], data['action'], data.get('value'))
    return jsonify({"success": True})

def process_game_action(room_code, player_name, action, value=None):
    room = rooms[room_code]
    state = room['gameState']
    pc = len(room['players'])

    if action == 'propose_team' and state["phase"] == "team_building":
        team = value.get('team', [])
        if len(team) != state["mission_team_sizes"][state["mission_number"] - 1]: return
        state["team_proposal"] = team; state["phase"] = "team_vote"; state["phase_text"] = "隊伍投票"; state["votes"] = {}

    elif action == 'vote_team' and state["phase"] == "team_vote":
        state["votes"][player_name] = value.get('vote')
        if len(state["votes"]) >= pc:
            process_team_vote_result(room_code)

    elif action == 'mission_vote' and state["phase"] == "mission_vote":
        state["mission_votes"][player_name] = value.get('vote')
        if len(state["mission_votes"]) >= len(state["team_proposal"]):
            process_mission_vote_result(room_code)

    elif action == 'use_lady' and state["phase"] == "lady_of_the_lake":
        if player_name != state["lady_holder"]: return
        target = value.get('target')
        if target == player_name or target in state["lady_used_on"]: return
        evil_roles = [r for r in state['roles_in_game'] if r in ALL_ROLES['evil']]
        loyalty = "善良" if state["assigned_roles"][target]["role"] not in evil_roles else "邪惡"
        state["assigned_roles"][player_name]["last_lady_reveal"] = f"你查驗了 {target}，他的陣營是: {loyalty}"
        state["lady_holder"] = target
        state["lady_used_missions"].append(state["mission_number"])
        state["lady_used_on"].append(target)
        advance_to_next_mission(room_code)

    elif action == 'assassinate' and state["phase"] == "assassination":
        target = value.get('target')
        merlin_list = [p for p, d in state["assigned_roles"].items() if d["role"] == "梅林"]
        if merlin_list and target == merlin_list[0]:
            end_game(room_code, 'evil', f"刺客 {player_name} 成功刺殺了梅林 ({target})！")
        else:
            end_game(room_code, 'good', f"刺客 {player_name} 刺殺失敗！")

    elif action == 'internal_check_vote_complete':
        if state['phase'] == 'team_vote' and len(state["votes"]) >= pc:
            process_team_vote_result(room_code)
        elif state['phase'] == 'mission_vote' and len(state["mission_votes"]) >= len(state["team_proposal"]):
            process_mission_vote_result(room_code)

def process_team_vote_result(room_code):
    room = rooms[room_code]
    state = room['gameState']
    pc = len(room['players'])
    approves = list(state["votes"].values()).count('approve')
    state["last_vote_details"] = [{"name": p['name'], "vote": state["votes"].get(p['name'])} for p in room['players']]
    if approves > pc / 2:
        state["phase"] = "mission_vote"; state["phase_text"] = "執行任務"; state["mission_votes"] = {}; state["vote_reject_count"] = 0
    else:
        advance_to_next_leader(room_code)

def process_mission_vote_result(room_code):
    room = rooms[room_code]
    state = room['gameState']
    pc = len(room['players'])
    fails = list(state["mission_votes"].values()).count('fail')
    is_two_fails_mission = TWO_FAILS_MISSION_REQUIRED.get(pc) == state["mission_number"]
    fail_threshold = 2 if is_two_fails_mission else 1
    is_success = fails < fail_threshold
    state["quest_track"][state["mission_number"] - 1] = "success" if is_success else "fail"
    state["mission_history"].append({
        "mission_num": state["mission_number"],
        "leader": state["player_order"][state["current_leader_index"]],
        "team": state["team_proposal"],
        "result": "success" if is_success else "fail",
        "fails": fails
    })
    check_game_phase_after_mission(room_code)

def end_game(room_code, winning_team, reason=""):
    state = rooms[room_code]['gameState']
    state["phase"] = "end"
    state["phase_text"] = "遊戲結束"
    
    # 建立一個角色到陣營的映射
    good_roles_set = set(ALL_ROLES['good'])
    
    all_roles_with_faction = []
    for p, d in state["assigned_roles"].items():
        role = d["role"]
        faction = "good" if role in good_roles_set else "evil"
        all_roles_with_faction.append({"name": p, "role": role, "faction": faction})

    state["game_over_data"] = {
        "winning_team": "善良陣營" if winning_team == "good" else "邪惡陣營",
        "reason": reason,
        "duration": time.time() * 1000 - state["game_start_time"],
        "all_roles": all_roles_with_faction, # 使用帶有陣營資訊的新列表
    }

def check_game_phase_after_mission(room_code):
    state = rooms[room_code]['gameState']
    mn = state["mission_number"]
    if state["quest_track"].count("fail") >= 3:
        end_game(room_code, 'evil', "壞人贏得了 3 個任務。")
        return
    if state["quest_track"].count("success") >= 3:
        if "刺客" in state["roles_in_game"]:
            state["phase"] = "assassination"; state["phase_text"] = "刺殺階段"
            return # <--- 這裡新增 return
        else:
            end_game(room_code, 'good', "好人贏得了 3 個任務 (無刺客)。")
            return
    if state.get("lady_holder") and mn in [2, 3, 4] and mn not in state.get("lady_used_missions", []):
        state["phase"] = "lady_of_the_lake"; state["phase_text"] = "湖中女神階段"
        return
    advance_to_next_mission(room_code)

def advance_to_next_mission(room_code):
    state = rooms[room_code]['gameState']
    pc = len(rooms[room_code]['players'])
    state["mission_number"] += 1
    state["current_leader_index"] = (state["current_leader_index"] + 1) % pc
    state["phase"] = "team_building"; state["phase_text"] = "組建隊伍"
    state["team_proposal"] = []; state["votes"] = {}; state["last_vote_details"] = None

def advance_to_next_leader(room_code):
    state = rooms[room_code]['gameState']
    state["vote_reject_count"] += 1
    if state["vote_reject_count"] >= 5:
        end_game(room_code, 'evil', "連續 5 次投票被否決。")
    else:
        pc = len(rooms[room_code]['players'])
        state["current_leader_index"] = (state["current_leader_index"] + 1) % pc
        state["phase"] = "team_building"; state["phase_text"] = "組建隊伍"
        state["team_proposal"] = []; state["votes"] = {}

# --- 背景巡邏員 ---
def reaper_task():
    while True:
        time.sleep(REAP_INTERVAL)
        with rooms_lock:
            current_time = time.time()
            rooms_to_delete = []
            for room_code, room in list(rooms.items()):
                if not room['players']:
                    rooms_to_delete.append(room_code)
                    continue
                    
                players_to_remove = []
                active_players = 0

                # --- 這是修改後的核心邏輯 ---
                for player in room['players']:
                    time_since_seen = current_time - player['last_seen']
                    is_connected = player['status'] == 'connected'

                    if room['gameState'] is None:  # 處理【大廳中】的玩家
                        if time_since_seen > LOBBY_KICK_TIMEOUT:
                            players_to_remove.append(player) # 超過 60 秒，加入踢除列表
                        elif time_since_seen > TIMEOUT_SECONDS and is_connected:
                            player['status'] = 'disconnected' # 超過 10 秒，僅標記為離線
                    
                    else:  # 處理【遊戲中】的玩家
                        if time_since_seen > TIMEOUT_SECONDS and is_connected:
                            player['status'] = 'disconnected' # 超過 10 秒，標記為離線
                    
                    if player['status'] == 'connected':
                        active_players += 1
                # --- 邏輯修改結束 ---

                if players_to_remove:
                    was_host_removed = any(p['isHost'] for p in players_to_remove)
                    
                    # ---【BUG修正】保留原始玩家順序 ---
                    removed_names = {p['name'] for p in players_to_remove}
                    room['players'] = [p for p in room['players'] if p not in players_to_remove]
                    room['lobbyPlayerOrder'] = [name for name in room['lobbyPlayerOrder'] if name not in removed_names]
                    # --- 順序修正結束 ---

                    if was_host_removed and room['players']:
                        room['players'][0]['isHost'] = True
                        room['players'][0]['isReady'] = True

                if not room['players'] or (current_time - room.get('created_at', current_time) > 3600 and active_players == 0):
                    rooms_to_delete.append(room_code)

            for code in rooms_to_delete:
                if code in rooms: del rooms[code]

        check_for_auto_actions()

def check_for_auto_actions():
    with rooms_lock:
        for room_code, room in list(rooms.items()):
            if not room.get('gameState'): continue

            state = room['gameState']
            pc = len(room['players'])
            if pc == 0: continue

            current_leader_name = state['player_order'][state['current_leader_index']]
            current_leader_player = find_player_by_name(room, current_leader_name)

            if state['phase'] == 'team_building' and current_leader_player and current_leader_player['status'] == 'disconnected':
                advance_to_next_leader(room_code)
                continue
            
            connected_players = [p for p in room['players'] if p['status'] == 'connected']
            all_connected_voted = lambda votes, players: all(p['name'] in votes for p in players)

            if state['phase'] == 'team_vote':
                if all_connected_voted(state['votes'], connected_players) and len(state['votes']) < pc:
                    for p in room['players']:
                        if p['status'] == 'disconnected' and p['name'] not in state['votes']:
                            state['votes'][p['name']] = 'reject'
                    process_game_action(room_code, "server", "internal_check_vote_complete")

            if state['phase'] == 'mission_vote':
                team_members = [find_player_by_name(room, name) for name in state['team_proposal']]
                connected_team_members = [p for p in team_members if p and p['status'] == 'connected']
                if all_connected_voted(state['mission_votes'], connected_team_members) and len(state['mission_votes']) < len(team_members):
                    for p in team_members:
                        if p and p['status'] == 'disconnected' and p['name'] not in state['mission_votes']:
                            state['mission_votes'][p['name']] = 'success'
                    process_game_action(room_code, "server", "internal_check_vote_complete")

# --- 伺服器啟動 ---
if __name__ == "__main__":
    reaper_thread = threading.Thread(target=reaper_task, daemon=True)
    reaper_thread.start()
    print("背景巡邏員已啟動。")
    # Render 會透過環境變數設定 PORT，若無則預設為 10000
    # 監聽 0.0.0.0 以便從外部訪問
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

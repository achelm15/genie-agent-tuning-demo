"""Shared definitions for the Genie agent tuning demo.

The notebooks import this file, so the data load, the Unity Catalog tuning and the
round-by-round agents all use one copy of every comment, metric, example and benchmark.
"""

import datetime as dt
import hashlib
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------------------
# MLB Stats API loader
# ---------------------------------------------------------------------------

API = "https://statsapi.mlb.com/api"


def get_json(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "databricks-genie-demo"})
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
            return json.loads(body), len(body)
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 ** i)


def schedule(season, team_id):
    url = f"{API}/v1/schedule?sportId=1&teamId={team_id}&season={season}&gameType=S,R"
    d, _ = get_json(url)
    games = [g for day in d["dates"] for g in day["games"]]
    # Final games only; a suspended game can show up on two dates
    seen, out = set(), []
    for g in games:
        if g["status"]["abstractGameState"] == "Final" and g["gamePk"] not in seen:
            seen.add(g["gamePk"])
            out.append(g["gamePk"])
    return out


def fetch_feed(game_pk):
    url = f"{API}/v1.1/game/{game_pk}/feed/live"
    t0 = time.time()
    feed, nbytes = get_json(url)
    return game_pk, url, feed, nbytes, int((time.time() - t0) * 1000)


def _int(v):
    return None if v is None or v == "" else int(v)


def _flt(v):
    return None if v is None or v == "" else float(v)


def parse_feed(feed):
    gd, ld = feed["gameData"], feed["liveData"]
    pk = gd["game"]["pk"]
    home_id, away_id = gd["teams"]["home"]["id"], gd["teams"]["away"]["id"]
    ls = ld["linescore"]["teams"]
    wx = gd.get("weather", {})
    gm = {
        "gm_pk": pk,
        "gm_dt": dt.date.fromisoformat(gd["datetime"]["officialDate"]),
        "ssn": int(gd["game"]["season"]),
        "gm_typ": gd["game"]["type"],
        "dbl_hdr": gd["game"]["doubleHeader"],
        "gm_num": _int(gd["game"].get("gameNumber")),
        "dn": gd["datetime"].get("dayNight"),
        "hm_tm_id": home_id,
        "aw_tm_id": away_id,
        "hm_r": _int(ls["home"].get("runs")),
        "aw_r": _int(ls["away"].get("runs")),
        "hm_h": _int(ls["home"].get("hits")),
        "aw_h": _int(ls["away"].get("hits")),
        "hm_e": _int(ls["home"].get("errors")),
        "aw_e": _int(ls["away"].get("errors")),
        "inn_ct": _int(ld["linescore"].get("currentInning")),
        "vnu_id": gd["venue"]["id"],
        "vnu_nm": gd["venue"]["name"],
        "wx_cond": wx.get("condition"),
        "wx_tmp": _int(wx.get("temp")),
        "wx_wnd": wx.get("wind"),
        "att": _int(gd.get("gameInfo", {}).get("attendance")),
        "dur_min": _int(gd.get("gameInfo", {}).get("gameDurationMinutes")),
    }

    teams = []
    for side in ("home", "away"):
        t = gd["teams"][side]
        teams.append({
            "tm_id": t["id"],
            "tm_abbr": t.get("abbreviation"),
            "tm_nm": t.get("name"),
            "tm_shrt": t.get("teamName"),
            "lg_nm": t.get("league", {}).get("name"),
            "div_nm": t.get("division", {}).get("name"),
        })

    players = []
    for p in gd["players"].values():
        players.append({
            "p_id": p["id"],
            "nm_full": p.get("fullName"),
            "nm_last": p.get("lastName"),
            "pos_cd": p.get("primaryPosition", {}).get("code"),
            "thr": p.get("pitchHand", {}).get("code"),
            "bat": p.get("batSide", {}).get("code"),
            "dob": dt.date.fromisoformat(p["birthDate"]) if p.get("birthDate") else None,
            "dbt_dt": dt.date.fromisoformat(p["mlbDebutDate"]) if p.get("mlbDebutDate") else None,
            "ht": p.get("height"),
            "wt": _int(p.get("weight")),
        })

    pgl = []
    for side in ("home", "away"):
        bs = ld["boxscore"]["teams"][side]
        tm_id = bs["team"]["id"]
        for order, pid in enumerate(bs.get("pitchers", []), start=1):
            s = bs["players"][f"ID{pid}"]["stats"].get("pitching", {})
            if not s:
                continue
            pgl.append({
                "gm_pk": pk,
                "p_id": pid,
                "tm_id": tm_id,
                "ord": order,
                "gs": _int(s.get("gamesStarted")) or 0,
                # Baseball notation: 5.2 means 5 and 2/3 innings. Left as-is on purpose.
                "ip": _flt(s.get("inningsPitched")),
                "outs": _int(s.get("outs")),
                "bf": _int(s.get("battersFaced")),
                "h": _int(s.get("hits")),
                "r": _int(s.get("runs")),
                "er": _int(s.get("earnedRuns")),
                "bb": _int(s.get("baseOnBalls")),
                "so": _int(s.get("strikeOuts")),
                "hr": _int(s.get("homeRuns")),
                "hbp": _int(s.get("hitByPitch")),
                "np": _int(s.get("numberOfPitches")),
                "w": _int(s.get("wins")),
                "l": _int(s.get("losses")),
                "sv": _int(s.get("saves")),
                "hld": _int(s.get("holds")),
                "bsv": _int(s.get("blownSaves")),
                "ir": _int(s.get("inheritedRunners")),
                "irs": _int(s.get("inheritedRunnersScored")),
            })

    pch = []
    for play in ld["plays"]["allPlays"]:
        about, res, mu = play["about"], play["result"], play["matchup"]
        fld_tm = home_id if about["isTopInning"] else away_id
        events = play["playEvents"]
        pitch_idx = [i for i, e in enumerate(events) if e.get("isPitch")]
        if not pitch_idx:
            continue
        last = pitch_idx[-1]
        # The feed stores the count after each event, so carry the previous one forward
        prev = {"balls": 0, "strikes": 0, "outs": events[0].get("count", {}).get("outs", 0)}
        for i, e in enumerate(events):
            if not e.get("isPitch"):
                prev = e.get("count", prev)
                continue
            det, pd_ = e.get("details", {}), e.get("pitchData", {}) or {}
            co, br, hd = pd_.get("coordinates", {}) or {}, pd_.get("breaks", {}) or {}, e.get("hitData", {}) or {}
            pch.append({
                "gm_pk": pk,
                "ab_idx": about["atBatIndex"],
                "evt_idx": e.get("index", i),
                "pch_seq": _int(e.get("pitchNumber")),
                "inn": about["inning"],
                "half": about["halfInning"],
                "fld_tm": fld_tm,
                "p_id": mu["pitcher"]["id"],
                "b_id": mu["batter"]["id"],
                "p_thr": mu.get("pitchHand", {}).get("code"),
                "b_sd": mu.get("batSide", {}).get("code"),
                "b_ct": _int(prev.get("balls")),
                "s_ct": _int(prev.get("strikes")),
                "o_ct": _int(prev.get("outs")),
                "pt_cd": (det.get("type") or {}).get("code"),
                "cd": det.get("code"),
                "spd": _flt(pd_.get("startSpeed")),
                "end_spd": _flt(pd_.get("endSpeed")),
                "spn": _int(br.get("spinRate")),
                "spn_dir": _int(br.get("spinDirection")),
                "ivb": _flt(br.get("breakVerticalInduced")),
                "hb": _flt(br.get("breakHorizontal")),
                "brk_ang": _flt(br.get("breakAngle")),
                "brk_len": _flt(br.get("breakLength")),
                "px": _flt(co.get("pX")),
                "pz": _flt(co.get("pZ")),
                "pfx_x": _flt(co.get("pfxX")),
                "pfx_z": _flt(co.get("pfxZ")),
                "vx0": _flt(co.get("vX0")),
                "vy0": _flt(co.get("vY0")),
                "vz0": _flt(co.get("vZ0")),
                "ax": _flt(co.get("aX")),
                "ay": _flt(co.get("aY")),
                "az": _flt(co.get("aZ")),
                "sz_top": _flt(pd_.get("strikeZoneTop")),
                "sz_bot": _flt(pd_.get("strikeZoneBottom")),
                "zn": _int(pd_.get("zone")),
                "ext": _flt(pd_.get("extension")),
                "plt_tm": _flt(pd_.get("plateTime")),
                "typ_conf": _flt(pd_.get("typeConfidence")),
                "ev": _flt(hd.get("launchSpeed")),
                "la": _flt(hd.get("launchAngle")),
                "dist": _flt(hd.get("totalDistance")),
                "traj": hd.get("trajectory"),
                "hrd": hd.get("hardness"),
                "loc": hd.get("location"),
                "is_lst": i == last,
                "ev_typ": res.get("eventType"),
                "play_id": e.get("playId"),
            })
            prev = e.get("count", prev)
    return gm, teams, players, pgl, pch


def load_season(season, team_id, workers=8):
    pks = schedule(season, team_id)
    print(f"{len(pks)} final games for team {team_id} in {season}")
    out = {"gm": [], "tm": {}, "plyr": {}, "pgl": [], "pch": [], "ld_log": []}
    batch = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for n, (pk, url, feed, nbytes, ms) in enumerate(ex.map(fetch_feed, pks), start=1):
            gm, teams, players, pgl, pch = parse_feed(feed)
            out["gm"].append(gm)
            for t in teams:
                out["tm"][t["tm_id"]] = t
            for p in players:
                out["plyr"][p["p_id"]] = p
            out["pgl"].extend(pgl)
            out["pch"].extend(pch)
            out["ld_log"].append({"btch_id": batch, "gm_pk": pk, "src_url": url, "http_sts": 200, "byt": nbytes,
                                  "dur_ms": ms, "ld_ts": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)})
            if n % 25 == 0:
                print(f"  {n}/{len(pks)} games")
    out["tm"] = list(out["tm"].values())
    out["plyr"] = list(out["plyr"].values())
    return out


SCHEMAS = {
    "gm": "gm_pk BIGINT, gm_dt DATE, ssn INT, gm_typ STRING, dbl_hdr STRING, gm_num INT, dn STRING, "
          "hm_tm_id INT, aw_tm_id INT, hm_r INT, aw_r INT, hm_h INT, aw_h INT, hm_e INT, aw_e INT, inn_ct INT, "
          "vnu_id INT, vnu_nm STRING, wx_cond STRING, wx_tmp INT, wx_wnd STRING, att INT, dur_min INT",
    "tm": "tm_id INT, tm_abbr STRING, tm_nm STRING, tm_shrt STRING, lg_nm STRING, div_nm STRING",
    "plyr": "p_id BIGINT, nm_full STRING, nm_last STRING, pos_cd STRING, thr STRING, bat STRING, dob DATE, "
            "dbt_dt DATE, ht STRING, wt INT",
    "pgl": "gm_pk BIGINT, p_id BIGINT, tm_id INT, ord INT, gs INT, ip DOUBLE, outs INT, bf INT, h INT, r INT, "
           "er INT, bb INT, so INT, hr INT, hbp INT, np INT, w INT, l INT, sv INT, hld INT, bsv INT, ir INT, irs INT",
    "pch": "gm_pk BIGINT, ab_idx INT, evt_idx INT, pch_seq INT, inn INT, half STRING, fld_tm INT, p_id BIGINT, "
           "b_id BIGINT, p_thr STRING, b_sd STRING, b_ct INT, s_ct INT, o_ct INT, pt_cd STRING, cd STRING, "
           "spd DOUBLE, end_spd DOUBLE, spn INT, spn_dir INT, ivb DOUBLE, hb DOUBLE, brk_ang DOUBLE, "
           "brk_len DOUBLE, px DOUBLE, pz DOUBLE, pfx_x DOUBLE, pfx_z DOUBLE, vx0 DOUBLE, vy0 DOUBLE, vz0 DOUBLE, "
           "ax DOUBLE, ay DOUBLE, az DOUBLE, sz_top DOUBLE, sz_bot DOUBLE, zn INT, ext DOUBLE, plt_tm DOUBLE, "
           "typ_conf DOUBLE, ev DOUBLE, la DOUBLE, dist DOUBLE, traj STRING, hrd STRING, loc STRING, "
           "is_lst BOOLEAN, ev_typ STRING, play_id STRING",
    "ld_log": "btch_id STRING, gm_pk BIGINT, src_url STRING, http_sts INT, byt BIGINT, dur_ms INT, ld_ts TIMESTAMP",
}


def drop_demo_objects(spark, s):
    """Drop everything the demo creates in schema s, in dependency order."""
    for v in ["pitching_metrics", "pitch_metrics"]:
        spark.sql(f"DROP VIEW IF EXISTS {s}.{v}")
    spark.sql(f"DROP FUNCTION IF EXISTS {s}.pitcher_season_line")
    for t in ["pch", "pgl", "ld_log", "gm", "plyr", "tm"]:
        spark.sql(f"DROP TABLE IF EXISTS {s}.{t}")


def write_tables(spark, data, s):
    """Write the six raw tables to schema s, with load metadata columns on the baseball tables."""
    from pyspark.sql import functions as F
    batch = data["ld_log"][0]["btch_id"]
    for name, ddl in SCHEMAS.items():
        cols = [c.strip().split(" ")[0] for c in ddl.split(",")]
        rows = [tuple(r[c] for c in cols) for r in data[name]]
        df = spark.createDataFrame(rows, schema=ddl)
        if name != "ld_log":
            df = (df.withColumn("_ld_ts", F.current_timestamp())
                    .withColumn("_src", F.lit("statsapi.mlb.com/api/v1.1/game/feed/live"))
                    .withColumn("_btch_id", F.lit(batch)))
        df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{s}.{name}")
        print(f"{s}.{name}: {len(rows):,} rows")


# ---------------------------------------------------------------------------
# Lever 2: Unity Catalog comments
# ---------------------------------------------------------------------------

TABLE_COMMENTS = {
    "gm": "One row per {short_name} game in the season, spring training and regular season, from the MLB Stats API. Key: gm_pk. Use this table for the team record (wins and losses) and one-run games. "
          "Join pgl and pch to this table on gm_pk to filter by date, game type, home or road, or final score.",
    "tm": "MLB teams, one row per team. tm_id is the team key used in gm, pgl and pch. The {name} are tm_id {id}, abbreviation {abbreviation}.",
    "plyr": "Players from both rosters, one row per player. Key: p_id. Use it for pitcher names (pgl.p_id, pch.p_id) and batter names (pch.b_id).",
    "pgl": "Pitcher game lines from the box score: one row per pitcher per game, for both teams. Key: gm_pk + p_id. "
           "Use this table for ERA, innings, strikeouts, walks, batters faced, starts, saves and quality starts. "
           "{short_name} pitchers are tm_id = {id}. ERA = 9 * SUM(er) / (SUM(outs) / 3).",
    "pch": "Pitch-by-pitch tracking data: one row per pitch thrown in these games, by both teams. Key: gm_pk + ab_idx + evt_idx. "
           "Use this table for pitch types, velocity, spin, movement, swings and whiffs, and contact quality. "
           "{short_name} pitchers are fld_tm = {id}. ev_typ repeats on every pitch of a plate appearance, so count outcomes only where is_lst = true.",
}

COLUMN_COMMENTS = {
    "gm": {
        "gm_pk": "Game ID (MLB gamePk). Primary key. Joins to pgl.gm_pk and pch.gm_pk.",
        "gm_dt": "Official game date.",
        "ssn": "Season year, for example 2026.",
        "gm_typ": "Game type: R = regular season, S = spring training.",
        "dbl_hdr": "Doubleheader flag: N = not a doubleheader, Y = traditional doubleheader, S = split doubleheader.",
        "gm_num": "Game number that day. 2 means the second game of a doubleheader.",
        "dn": "day or night game.",
        "hm_tm_id": "Home team ID. Joins to tm.tm_id. When hm_tm_id = {id} the {short_name} are at home, at {home_park}.",
        "aw_tm_id": "Away team ID. Joins to tm.tm_id.",
        "hm_r": "Final runs scored by the home team.",
        "aw_r": "Final runs scored by the away team. Run margin is ABS(hm_r - aw_r); a one-run game has a margin of 1.",
        "hm_h": "Hits by the home team.",
        "aw_h": "Hits by the away team.",
        "hm_e": "Errors by the home team.",
        "aw_e": "Errors by the away team.",
        "inn_ct": "Innings played. More than 9 means extra innings.",
        "vnu_id": "Ballpark ID.",
        "vnu_nm": "Ballpark name, for example {home_park}.",
        "wx_cond": "Weather at first pitch, for example Sunny or Partly Cloudy.",
        "wx_tmp": "Temperature at first pitch, degrees Fahrenheit.",
        "wx_wnd": "Wind at first pitch as text, for example 12 mph, In From CF.",
        "att": "Attendance.",
        "dur_min": "Game length in minutes.",
    },
    "tm": {
        "tm_id": "Team ID. Primary key. {short_name} = {id}.",
        "tm_abbr": "Team abbreviation, for example {abbreviation}.",
        "tm_nm": "Full team name, for example {name}.",
        "tm_shrt": "Team nickname, for example {short_name}.",
        "lg_nm": "League: American League or National League.",
        "div_nm": "Division, for example National League West.",
    },
    "plyr": {
        "p_id": "Player ID (MLB person ID). Primary key.",
        "nm_full": "Player full name, for example {pitcher}. Use for pitcher and batter names.",
        "nm_last": "Player last name.",
        "pos_cd": "Primary position code: 1 = pitcher, 2 = catcher, 3 = first base, 4 = second base, 5 = third base, "
                  "6 = shortstop, 7 = left field, 8 = center field, 9 = right field, 10 = designated hitter, Y = two-way player, O = outfielder.",
        "thr": "Throwing hand: L = left, R = right.",
        "bat": "Batting side: L = left, R = right, S = switch hitter.",
        "dob": "Date of birth.",
        "dbt_dt": "MLB debut date.",
        "ht": "Height as text, for example 6 ft 2 in.",
        "wt": "Weight in pounds.",
    },
    "pgl": {
        "gm_pk": "Game ID. Joins to gm.gm_pk (many to one).",
        "p_id": "Pitcher ID. Joins to plyr.p_id.",
        "tm_id": "Team the pitcher pitched for. Joins to tm.tm_id. {short_name} pitchers are tm_id = {id}.",
        "ord": "Order the pitcher entered the game. 1 is the starter.",
        "gs": "1 if the pitcher started the game, 0 if he pitched in relief. Starters, the rotation: gs = 1. Relievers, the bullpen: gs = 0.",
        "ip": "Innings pitched in baseball notation, where 5.2 means 5 and 2/3 innings, not 5.2. Never sum or average this column. Innings = SUM(outs) / 3.",
        "outs": "Outs recorded. Use this for innings pitched: SUM(outs) / 3. Quality start: gs = 1 AND outs >= 18 AND er <= 3.",
        "bf": "Batters faced (TBF). Denominator for strikeout rate: K% = SUM(so) / SUM(bf). Walk rate: BB% = SUM(bb) / SUM(bf).",
        "h": "Hits allowed.",
        "r": "Runs allowed, earned and unearned.",
        "er": "Earned runs allowed. ERA = 9 * SUM(er) / (SUM(outs) / 3).",
        "bb": "Walks allowed (bases on balls), including intentional walks.",
        "so": "Strikeouts. WHIP = (SUM(bb) + SUM(h)) / (SUM(outs) / 3).",
        "hr": "Home runs allowed.",
        "hbp": "Batters hit by a pitch.",
        "np": "Pitches thrown.",
        "w": "1 if the pitcher was credited with the win.",
        "l": "1 if the pitcher was charged with the loss.",
        "sv": "1 if the pitcher recorded a save.",
        "hld": "1 if the pitcher recorded a hold.",
        "bsv": "1 if the pitcher blew a save.",
        "ir": "Inherited runners: runners on base when the pitcher entered.",
        "irs": "Inherited runners who scored.",
    },
    "pch": {
        "gm_pk": "Game ID. Joins to gm.gm_pk (many to one).",
        "ab_idx": "Plate appearance number within the game, starting at 0.",
        "evt_idx": "Event number within the plate appearance. With gm_pk and ab_idx, uniquely identifies a pitch.",
        "pch_seq": "Pitch number within the plate appearance. 1 is the first pitch.",
        "inn": "Inning.",
        "half": "Half inning: top or bottom.",
        "fld_tm": "Team ID of the pitching (fielding) team. Joins to tm.tm_id. Pitches thrown by {short_name} pitchers are fld_tm = {id}.",
        "p_id": "Pitcher ID. Joins to plyr.p_id.",
        "b_id": "Batter ID. Joins to plyr.p_id.",
        "p_thr": "Pitcher throwing hand: L or R.",
        "b_sd": "Side the batter hit from in this plate appearance: L or R.",
        "b_ct": "Balls in the count before this pitch.",
        "s_ct": "Strikes in the count before this pitch. The count is b_ct-s_ct, for example 3-2.",
        "o_ct": "Outs in the inning before this pitch.",
        "pt_cd": "Pitch type code: FF = four-seam fastball, SI = sinker, FC = cutter, SL = slider, ST = sweeper, SV = slurve, "
                 "CU = curveball, KC = knuckle curve, CS = slow curve, CH = changeup, FS = splitter, FO = forkball, "
                 "KN = knuckleball, EP = eephus, FA = fastball (unclassified), UN = unknown.",
        "cd": "Pitch result code: B = ball, *B = ball in dirt, C = called strike, S = swinging strike, W = swinging strike blocked, "
              "T = foul tip, F = foul, L = foul bunt, M = missed bunt, O = foul tip on bunt, X = in play out, D = in play no out, "
              "E = in play run scored, H = hit by pitch, P = pitchout. Swings: S, W, T, F, L, M, O, X, D, E. Whiffs: S, W, M. "
              "Whiff rate = whiffs / swings. Balls in play: X, D, E. Hard-hit rate uses only balls in play where ev IS NOT NULL.",
        "spd": "Pitch velocity at release, mph (velo).",
        "end_spd": "Pitch velocity at the plate, mph.",
        "spn": "Spin rate, rpm.",
        "spn_dir": "Spin direction, degrees.",
        "ivb": "Induced vertical break, inches. Higher means more rise on a fastball.",
        "hb": "Horizontal break, inches.",
        "brk_ang": "Break angle, degrees.",
        "brk_len": "Break length, inches.",
        "px": "Horizontal plate location, feet from the middle of the plate, catcher view.",
        "pz": "Vertical plate location, feet above the ground.",
        "pfx_x": "Horizontal movement (PITCHf/x), raw tracking value.",
        "pfx_z": "Vertical movement (PITCHf/x), raw tracking value.",
        "vx0": "Release velocity x component, raw tracking value.",
        "vy0": "Release velocity y component, raw tracking value.",
        "vz0": "Release velocity z component, raw tracking value.",
        "ax": "Acceleration x, raw tracking value.",
        "ay": "Acceleration y, raw tracking value.",
        "az": "Acceleration z, raw tracking value.",
        "sz_top": "Top of the batter strike zone, feet.",
        "sz_bot": "Bottom of the batter strike zone, feet.",
        "zn": "Gameday zone: 1 to 9 are inside the strike zone, 11 to 14 are outside it.",
        "ext": "Release extension, feet.",
        "plt_tm": "Time from release to plate, seconds.",
        "typ_conf": "Pitch type classification confidence.",
        "ev": "Exit velocity, mph, on balls in play only. Hard-hit ball: ev >= 95 on a ball in play (cd in X, D, E). Hard-hit rate = hard-hit balls / balls in play with ev recorded (cd in X, D, E AND ev IS NOT NULL). A few balls in play have no exit velocity, so dividing by all balls in play understates the rate.",
        "la": "Launch angle, degrees, on balls in play only.",
        "dist": "Projected distance, feet, on balls in play only.",
        "traj": "Batted ball type: ground_ball, line_drive, fly_ball, popup.",
        "hrd": "Contact hardness label from the feed: soft, medium, hard.",
        "loc": "Fielder position code where the ball was fielded.",
        "is_lst": "true on the last pitch of the plate appearance. Filter is_lst = true when counting plate appearance outcomes such as strikeouts, walks or home runs.",
        "ev_typ": "Plate appearance result, for example strikeout, walk, single, home_run, field_out. Repeated on every pitch of the plate appearance, "
                  "so count it only where is_lst = true or totals will be several times too high. Strikeouts: ev_typ IN (strikeout, strikeout_double_play).",
        "play_id": "Tracking ID for the pitch.",
    },
}
LOAD_COLS = {
    "_ld_ts": "Load timestamp. Pipeline metadata.",
    "_src": "Source endpoint. Pipeline metadata.",
    "_btch_id": "Load batch ID. Pipeline metadata.",
}


def q(text):
    return text.replace("\\", "\\\\").replace("'", "\\'")


def apply_comments(spark, s, team):
    """The comments name the team, so they're filled in from it here."""
    for t, comment in TABLE_COMMENTS.items():
        spark.sql(f"COMMENT ON TABLE {s}.{t} IS '{q(comment.format(**team))}'")
        for col, c in {**COLUMN_COMMENTS[t], **LOAD_COLS}.items():
            spark.sql(f"ALTER TABLE {s}.{t} ALTER COLUMN `{col}` COMMENT '{q(c.format(**team))}'")
        print(f"Commented {t}")


# ---------------------------------------------------------------------------
# Lever 3: primary and foreign keys
# ---------------------------------------------------------------------------

KEYS = {
    "tm": ["tm_id"],
    "plyr": ["p_id"],
    "gm": ["gm_pk"],
    "pgl": ["gm_pk", "p_id"],
    "pch": ["gm_pk", "ab_idx", "evt_idx"],
}
FOREIGN_KEYS = [
    ("gm", "hm_tm_id", "tm", "tm_id"),
    ("gm", "aw_tm_id", "tm", "tm_id"),
    ("pgl", "gm_pk", "gm", "gm_pk"),
    ("pgl", "p_id", "plyr", "p_id"),
    ("pgl", "tm_id", "tm", "tm_id"),
    ("pch", "gm_pk", "gm", "gm_pk"),
    ("pch", "p_id", "plyr", "p_id"),
    ("pch", "b_id", "plyr", "p_id"),
    ("pch", "fld_tm", "tm", "tm_id"),
]


def apply_keys(spark, s):
    # Drop foreign keys before primary keys so this can be rerun
    for t, c, _, _ in FOREIGN_KEYS:
        spark.sql(f"ALTER TABLE {s}.{t} DROP CONSTRAINT IF EXISTS {t}_{c}_fk")
    for t in KEYS:
        spark.sql(f"ALTER TABLE {s}.{t} DROP CONSTRAINT IF EXISTS {t}_pk")
    for t, cols in KEYS.items():
        for c in cols:
            spark.sql(f"ALTER TABLE {s}.{t} ALTER COLUMN {c} SET NOT NULL")
        spark.sql(f"ALTER TABLE {s}.{t} ADD CONSTRAINT {t}_pk PRIMARY KEY ({', '.join(cols)})")
    for t, c, ref, ref_c in FOREIGN_KEYS:
        spark.sql(f"ALTER TABLE {s}.{t} ADD CONSTRAINT {t}_{c}_fk FOREIGN KEY ({c}) REFERENCES {s}.{ref} ({ref_c})")
    print(f"Added {len(KEYS)} primary keys and {len(FOREIGN_KEYS)} foreign keys")


# ---------------------------------------------------------------------------
# Lever 4: metric views. sp is the plain catalog.schema (YAML can't start with a backtick).
# ---------------------------------------------------------------------------

def pitching_metrics_yaml(sp, team):
    short, name, park = team["short_name"], team["name"], team["home_park"]
    return f"""
version: 1.1
source: {sp}.pgl
comment: "Regular season {short} and opponent pitching KPIs from box score lines (spring training is excluded, as in MLB's official stats), one row per pitcher appearance (pitcher per game), not one row per game. Don't count games, wins or losses from this view: a game appears once for every pitcher who pitched in it. Use gm for team records. {short} pitchers are Team = '{name}'."
joins:
  - name: dim_game
    source: {sp}.gm
    'on': source.gm_pk = dim_game.gm_pk
  - name: dim_pitcher
    source: {sp}.plyr
    'on': source.p_id = dim_pitcher.p_id
  - name: dim_team
    source: {sp}.tm
    'on': source.tm_id = dim_team.tm_id
filter: dim_game.gm_typ = 'R'
dimensions:
  - name: Season
    expr: dim_game.ssn
  - name: Game Type
    expr: "CASE dim_game.gm_typ WHEN 'R' THEN 'Regular season' WHEN 'S' THEN 'Spring training' ELSE dim_game.gm_typ END"
    comment: "Always 'Regular season' in this view. Spring training isn't included."
    synonyms: [season type, regular season, spring training]
  - name: Game Date
    expr: dim_game.gm_dt
  - name: Month Number
    expr: MONTH(dim_game.gm_dt)
  - name: Month Name
    expr: "DATE_FORMAT(dim_game.gm_dt, 'MMMM')"
  - name: Pitcher
    expr: dim_pitcher.nm_full
  - name: Pitcher Throws
    expr: dim_pitcher.thr
  - name: Team
    expr: dim_team.tm_nm
    comment: "Full team name of the pitcher's team. The {short} are exactly '{name}'."
    synonyms: [club]
  - name: Team Abbreviation
    expr: dim_team.tm_abbr
  - name: Role
    expr: "CASE WHEN source.gs = 1 THEN 'Starter' ELSE 'Reliever' END"
    comment: "Exact values: 'Starter' (started the game) or 'Reliever' (pitched in relief, the bullpen)."
    synonyms: [bullpen, rotation, starter, reliever]
  - name: Home or Road
    expr: "CASE WHEN source.tm_id = dim_game.hm_tm_id THEN 'Home' ELSE 'Road' END"
    comment: "Exact values: 'Home' (at {park} for the {short}) or 'Road'."
    synonyms: [home, away, road, {park}]
  - name: Venue
    expr: dim_game.vnu_nm
    synonyms: [ballpark, stadium, park]
  - name: Run Margin
    expr: ABS(dim_game.hm_r - dim_game.aw_r)
    comment: "Final margin of the game in runs. A one-run game has a Run Margin of 1."
  - name: Game Result
    expr: "CASE WHEN (source.tm_id = dim_game.hm_tm_id) = (dim_game.hm_r > dim_game.aw_r) THEN 'Win' ELSE 'Loss' END"
    comment: "Whether the pitcher's team won or lost the game. Use it to split pitching stats by wins and losses, not to count a team's wins or losses: each game repeats once per pitcher. Team records come from gm."
measures:
  - name: Appearances
    expr: COUNT(1)
  - name: Games Started
    expr: SUM(source.gs)
  - name: Innings Pitched
    expr: "ROUND(SUM(source.outs) / 3.0, 1)"
    comment: "Innings as a decimal (outs / 3), rounded to one place, so 5 and 2/3 innings is 5.7."
    synonyms: [IP, innings]
  - name: Earned Runs
    expr: SUM(source.er)
  - name: Strikeouts
    expr: SUM(source.so)
    synonyms: [Ks, punchouts, SO]
  - name: Walks
    expr: SUM(source.bb)
    synonyms: [BB, bases on balls]
  - name: Hits Allowed
    expr: SUM(source.h)
  - name: Home Runs Allowed
    expr: SUM(source.hr)
    synonyms: [HR allowed, homers allowed]
  - name: Batters Faced
    expr: SUM(source.bf)
    synonyms: [TBF]
  - name: ERA
    expr: "ROUND(9 * SUM(source.er) / NULLIF(SUM(source.outs) / 3.0, 0), 2)"
    comment: "Earned run average: earned runs per nine innings."
    synonyms: [earned run average]
  - name: WHIP
    expr: "ROUND((SUM(source.bb) + SUM(source.h)) / NULLIF(SUM(source.outs) / 3.0, 0), 2)"
    comment: "Walks plus hits per inning pitched."
  - name: Strikeout Rate
    expr: "ROUND(100.0 * SUM(source.so) / NULLIF(SUM(source.bf), 0), 1)"
    comment: "Strikeouts per 100 batters faced (K%), on a 0 to 100 scale."
    synonyms: [K%, K rate, strikeout percentage]
  - name: Walk Rate
    expr: "ROUND(100.0 * SUM(source.bb) / NULLIF(SUM(source.bf), 0), 1)"
    comment: "Walks per 100 batters faced (BB%), on a 0 to 100 scale."
    synonyms: [BB%, walk percentage]
  - name: K Minus BB Rate
    expr: "ROUND(100.0 * (SUM(source.so) - SUM(source.bb)) / NULLIF(SUM(source.bf), 0), 1)"
    synonyms: [K-BB%]
  - name: HR per 9
    expr: "ROUND(9 * SUM(source.hr) / NULLIF(SUM(source.outs) / 3.0, 0), 2)"
    synonyms: [HR/9]
  - name: Quality Starts
    expr: COUNT(1) FILTER (WHERE source.gs = 1 AND source.outs >= 18 AND source.er <= 3)
    comment: "Starts of at least 6 innings with 3 or fewer earned runs."
    synonyms: [QS]
  - name: Saves
    expr: SUM(source.sv)
  - name: Blown Saves
    expr: SUM(source.bsv)
  - name: Holds
    expr: SUM(source.hld)
"""


def pitch_metrics_yaml(sp, team):
    short, name, park = team["short_name"], team["name"], team["home_park"]
    return f"""
version: 1.1
source: {sp}.pch
comment: "Pitch tracking KPIs, one row per pitch, both teams. Pitches by {short} pitchers are Pitching Team = '{name}'."
joins:
  - name: dim_game
    source: {sp}.gm
    'on': source.gm_pk = dim_game.gm_pk
  - name: dim_pitcher
    source: {sp}.plyr
    'on': source.p_id = dim_pitcher.p_id
  - name: dim_batter
    source: {sp}.plyr
    'on': source.b_id = dim_batter.p_id
  - name: dim_team
    source: {sp}.tm
    'on': source.fld_tm = dim_team.tm_id
dimensions:
  - name: Season
    expr: dim_game.ssn
  - name: Game Type
    expr: "CASE dim_game.gm_typ WHEN 'R' THEN 'Regular season' WHEN 'S' THEN 'Spring training' ELSE dim_game.gm_typ END"
    comment: "Exact values: 'Regular season' or 'Spring training'. Filter on these labels, not the codes R and S."
    synonyms: [season type, regular season, spring training]
  - name: Game Date
    expr: dim_game.gm_dt
  - name: Month Number
    expr: MONTH(dim_game.gm_dt)
  - name: Month Name
    expr: "DATE_FORMAT(dim_game.gm_dt, 'MMMM')"
  - name: Pitcher
    expr: dim_pitcher.nm_full
  - name: Pitcher Throws
    expr: source.p_thr
  - name: Batter
    expr: dim_batter.nm_full
  - name: Batter Side
    expr: source.b_sd
    synonyms: [lefties, righties, handedness]
  - name: Pitching Team
    expr: dim_team.tm_nm
    comment: "Full team name of the pitching team. {short} pitchers are exactly '{name}'."
    synonyms: [team]
  - name: Pitch Type
    expr: |
      CASE source.pt_cd
        WHEN 'FF' THEN 'Four-seam fastball' WHEN 'SI' THEN 'Sinker' WHEN 'FC' THEN 'Cutter'
        WHEN 'SL' THEN 'Slider' WHEN 'ST' THEN 'Sweeper' WHEN 'SV' THEN 'Slurve'
        WHEN 'CU' THEN 'Curveball' WHEN 'KC' THEN 'Knuckle curve' WHEN 'CS' THEN 'Slow curve'
        WHEN 'CH' THEN 'Changeup' WHEN 'FS' THEN 'Splitter' WHEN 'FO' THEN 'Forkball'
        WHEN 'KN' THEN 'Knuckleball' WHEN 'EP' THEN 'Eephus' WHEN 'FA' THEN 'Fastball'
        ELSE 'Unknown'
      END
    comment: "Pitch type name, for example 'Four-seam fastball', 'Slider', 'Sweeper', 'Changeup'. Use this rather than Pitch Type Code."
    synonyms: [pitch, pitch name]
  - name: Pitch Type Code
    expr: source.pt_cd
  - name: Ball-Strike Count
    expr: "CONCAT(source.b_ct, '-', source.s_ct)"
    comment: "Balls-strikes count before the pitch, for example 3-2."
  - name: Inning
    expr: source.inn
  - name: Home or Road
    expr: "CASE WHEN source.fld_tm = dim_game.hm_tm_id THEN 'Home' ELSE 'Road' END"
    comment: "Exact values: 'Home' (at {park} for the {short}) or 'Road'."
    synonyms: [home, away, road, {park}]
  - name: Venue
    expr: dim_game.vnu_nm
  - name: Zone
    expr: "CASE WHEN source.zn BETWEEN 1 AND 9 THEN 'In zone' ELSE 'Out of zone' END"
    comment: "Exact values: 'In zone' or 'Out of zone'."
  - name: Batted Ball Type
    expr: source.traj
measures:
  - name: Pitches
    expr: COUNT(1)
  - name: Avg Velocity
    expr: "ROUND(AVG(source.spd), 1)"
    synonyms: [velo, average velocity, mph]
  - name: Max Velocity
    expr: "ROUND(MAX(source.spd), 1)"
  - name: Avg Spin Rate
    expr: "ROUND(AVG(source.spn), 0)"
  - name: Avg Induced Vertical Break
    expr: "ROUND(AVG(source.ivb), 1)"
    synonyms: [IVB, ride]
  - name: Avg Horizontal Break
    expr: "ROUND(AVG(source.hb), 1)"
  - name: Swings
    expr: "COUNT(1) FILTER (WHERE source.cd IN ('S','W','T','F','L','M','O','X','D','E'))"
  - name: Whiffs
    expr: "COUNT(1) FILTER (WHERE source.cd IN ('S','W','M'))"
    synonyms: [swinging strikes, swing and miss]
  - name: Whiff Rate
    expr: "ROUND(100.0 * COUNT(1) FILTER (WHERE source.cd IN ('S','W','M')) / NULLIF(COUNT(1) FILTER (WHERE source.cd IN ('S','W','T','F','L','M','O','X','D','E')), 0), 1)"
    comment: "Whiffs per 100 swings, on a 0 to 100 scale."
    synonyms: [whiff %, swing and miss rate, miss rate]
  - name: CSW Rate
    expr: "ROUND(100.0 * COUNT(1) FILTER (WHERE source.cd IN ('C','S','W','M')) / NULLIF(COUNT(1), 0), 1)"
    comment: "Called strikes plus whiffs per 100 pitches."
    synonyms: [CSW%, called strikes plus whiffs]
  - name: Zone Rate
    expr: "ROUND(100.0 * COUNT(1) FILTER (WHERE source.zn BETWEEN 1 AND 9) / NULLIF(COUNT(1) FILTER (WHERE source.zn IS NOT NULL), 0), 1)"
  - name: Chase Rate
    expr: "ROUND(100.0 * COUNT(1) FILTER (WHERE source.zn > 9 AND source.cd IN ('S','W','T','F','L','M','O','X','D','E')) / NULLIF(COUNT(1) FILTER (WHERE source.zn > 9), 0), 1)"
    comment: "Swings at pitches outside the zone per 100 pitches outside the zone."
    synonyms: [chase %, O-swing]
  - name: Balls In Play
    expr: "COUNT(1) FILTER (WHERE source.cd IN ('X','D','E'))"
  - name: Hard-Hit Rate
    expr: "ROUND(100.0 * COUNT(1) FILTER (WHERE source.cd IN ('X','D','E') AND source.ev >= 95) / NULLIF(COUNT(1) FILTER (WHERE source.cd IN ('X','D','E') AND source.ev IS NOT NULL), 0), 1)"
    comment: "Balls in play hit 95 mph or harder per 100 tracked balls in play."
    synonyms: [hard hit %, hard-hit percentage]
  - name: Avg Exit Velocity
    expr: "ROUND(AVG(source.ev) FILTER (WHERE source.cd IN ('X','D','E')), 1)"
    synonyms: [exit velo, EV]
  - name: Avg Launch Angle
    expr: "ROUND(AVG(source.la) FILTER (WHERE source.cd IN ('X','D','E')), 1)"
  - name: Strikeouts
    expr: "COUNT(1) FILTER (WHERE source.is_lst AND source.ev_typ IN ('strikeout', 'strikeout_double_play'))"
    comment: "Plate appearances ending in a strikeout, credited to the pitch that ended them."
    synonyms: [Ks, punchouts]
  - name: Walks
    expr: "COUNT(1) FILTER (WHERE source.is_lst AND source.ev_typ IN ('walk', 'intent_walk'))"
  - name: Home Runs Allowed
    expr: "COUNT(1) FILTER (WHERE source.is_lst AND source.ev_typ = 'home_run')"
"""


def create_metric_views(spark, s, sp, team):
    spark.sql(f"CREATE OR REPLACE VIEW {s}.pitching_metrics WITH METRICS LANGUAGE YAML AS $${pitching_metrics_yaml(sp, team)}$$")
    spark.sql(f"CREATE OR REPLACE VIEW {s}.pitch_metrics WITH METRICS LANGUAGE YAML AS $${pitch_metrics_yaml(sp, team)}$$")
    print("Created pitching_metrics and pitch_metrics")


# ---------------------------------------------------------------------------
# Lever 5: trusted function and example SQL
# ---------------------------------------------------------------------------

def create_function(spark, s, season, team):
    spark.sql(f"""
CREATE OR REPLACE FUNCTION {s}.pitcher_season_line(
  pitcher_name STRING COMMENT 'Part of the pitcher name, for example {q(team['pitcher'])}'
)
RETURNS TABLE (pitcher STRING, team STRING, role STRING, games INT, innings DOUBLE, era DOUBLE,
               whip DOUBLE, k_pct DOUBLE, bb_pct DOUBLE, quality_starts INT)
COMMENT 'Regular season pitching line for a pitcher, split by starter and reliever appearances. Trusted definitions of ERA, WHIP, K% and BB%.'
RETURN
  SELECT p.nm_full,
         t.tm_abbr,
         CASE WHEN l.gs = 1 THEN 'Starter' ELSE 'Reliever' END,
         CAST(COUNT(*) AS INT),
         CAST(ROUND(SUM(l.outs) / 3.0, 1) AS DOUBLE),
         CAST(ROUND(9 * SUM(l.er) / NULLIF(SUM(l.outs) / 3.0, 0), 2) AS DOUBLE),
         CAST(ROUND((SUM(l.bb) + SUM(l.h)) / NULLIF(SUM(l.outs) / 3.0, 0), 2) AS DOUBLE),
         CAST(ROUND(100.0 * SUM(l.so) / NULLIF(SUM(l.bf), 0), 1) AS DOUBLE),
         CAST(ROUND(100.0 * SUM(l.bb) / NULLIF(SUM(l.bf), 0), 1) AS DOUBLE),
         CAST(COUNT_IF(l.gs = 1 AND l.outs >= 18 AND l.er <= 3) AS INT)
  FROM {s}.pgl l
  JOIN {s}.gm g ON l.gm_pk = g.gm_pk
  JOIN {s}.plyr p ON l.p_id = p.p_id
  JOIN {s}.tm t ON l.tm_id = t.tm_id
  WHERE g.gm_typ = 'R' AND g.ssn = {season}
    AND p.nm_full ILIKE CONCAT('%', pitcher_name, '%')
  GROUP BY p.nm_full, t.tm_abbr, CASE WHEN l.gs = 1 THEN 'Starter' ELSE 'Reliever' END
""")
    print("Created pitcher_season_line")


PITCH_NAME_CASE = """CASE x.pt_cd
         WHEN 'FF' THEN 'Four-seam fastball' WHEN 'SI' THEN 'Sinker' WHEN 'FC' THEN 'Cutter'
         WHEN 'SL' THEN 'Slider' WHEN 'ST' THEN 'Sweeper' WHEN 'SV' THEN 'Slurve'
         WHEN 'CU' THEN 'Curveball' WHEN 'KC' THEN 'Knuckle curve' WHEN 'CH' THEN 'Changeup'
         WHEN 'FS' THEN 'Splitter' ELSE x.pt_cd END"""


def examples(s, team, season):
    """(question title, SQL). The arsenal query takes a :pitcher_name parameter."""
    tid = team["id"]
    return [
    ("How did our bullpen do in close games?",
     f"""-- Close game = final margin of 2 runs or fewer. Relievers = gs = 0. Regular season only.
SELECT ROUND(SUM(l.outs) / 3.0, 1) AS innings,
       ROUND(9 * SUM(l.er) / (SUM(l.outs) / 3.0), 2) AS era,
       ROUND((SUM(l.bb) + SUM(l.h)) / (SUM(l.outs) / 3.0), 2) AS whip,
       ROUND(100.0 * SUM(l.so) / SUM(l.bf), 1) AS k_pct,
       ROUND(100.0 * SUM(l.bb) / SUM(l.bf), 1) AS bb_pct
FROM {s}.pgl l
JOIN {s}.gm g ON l.gm_pk = g.gm_pk
WHERE l.tm_id = {tid} AND g.gm_typ = 'R' AND g.ssn = {season}
  AND l.gs = 0
  AND ABS(g.hm_r - g.aw_r) <= 2"""),

    (f"What does {team['pitcher']}'s arsenal look like?",
     f"""SELECT {PITCH_NAME_CASE} AS pitch_type,
       COUNT(*) AS pitches,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS usage_pct,
       ROUND(AVG(x.spd), 1) AS avg_velo,
       ROUND(AVG(x.spn), 0) AS avg_spin,
       ROUND(AVG(x.ivb), 1) AS avg_ivb,
       ROUND(AVG(x.hb), 1) AS avg_hb,
       ROUND(100.0 * COUNT_IF(x.cd IN ('S','W','M'))
             / NULLIF(COUNT_IF(x.cd IN ('S','W','T','F','L','M','O','X','D','E')), 0), 1) AS whiff_pct
FROM {s}.pch x
JOIN {s}.gm g ON x.gm_pk = g.gm_pk
JOIN {s}.plyr p ON x.p_id = p.p_id
WHERE p.nm_full ILIKE CONCAT('%', :pitcher_name, '%')
  AND g.gm_typ = 'R' AND g.ssn = {season}
GROUP BY 1
ORDER BY pitches DESC"""),

    ("Which of our starters pitched deepest into games?",
     f"""-- Innings = outs / 3. Quality start = 6+ innings and 3 or fewer earned runs.
SELECT p.nm_full AS pitcher,
       COUNT(*) AS starts,
       ROUND(SUM(l.outs) / 3.0 / COUNT(*), 2) AS innings_per_start,
       COUNT_IF(l.outs >= 18 AND l.er <= 3) AS quality_starts,
       ROUND(9 * SUM(l.er) / (SUM(l.outs) / 3.0), 2) AS era
FROM {s}.pgl l
JOIN {s}.gm g ON l.gm_pk = g.gm_pk
JOIN {s}.plyr p ON l.p_id = p.p_id
WHERE l.tm_id = {tid} AND g.gm_typ = 'R' AND g.ssn = {season}
  AND l.gs = 1
GROUP BY p.nm_full
HAVING COUNT(*) >= 5
ORDER BY innings_per_start DESC"""),

    ("What pitches did our staff use to finish strikeouts?",
     f"""-- Count plate appearance outcomes on the last pitch only (is_lst), or every pitch of the at-bat gets counted.
SELECT {PITCH_NAME_CASE} AS pitch_type,
       COUNT(*) AS strikeouts,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct
FROM {s}.pch x
JOIN {s}.gm g ON x.gm_pk = g.gm_pk
WHERE x.fld_tm = {tid} AND g.gm_typ = 'R' AND g.ssn = {season}
  AND x.is_lst
  AND x.ev_typ IN ('strikeout', 'strikeout_double_play')
GROUP BY 1
ORDER BY strikeouts DESC"""),
    ]


def example_parameters(team):
    return {"pitcher_name": ("STRING", f"Part of the pitcher name, for example {team['pitcher']}")}


# ---------------------------------------------------------------------------
# Lever 6: general instructions
# ---------------------------------------------------------------------------

def instructions(season, team):
    return f"""- This agent covers the {team['name']} pitching staff. "We", "us" and "our" mean the {team['short_name']} (team ID {team['id']}, {team['abbreviation']}).
- Use the {season} regular season unless someone asks for spring training or another period: filter Game Type = 'Regular season' (gm_typ = 'R'). That applies to "this season", "this year", "in {season}", and to questions that don't mention a period at all.
- The bullpen means relief appearances: Role = 'Reliever' in the metric views, gs = 0 in pgl. The rotation means starts: Role = 'Starter', gs = 1. Use these exact values, not a pattern match.
- For any KPI defined in pitching_metrics or pitch_metrics (ERA, WHIP, K%, BB%, whiff rate, hard-hit rate, velocity and others), use the metric view measure rather than recalculating it.
- Round in the final SELECT: ROUND(x, 2) for ERA, WHIP and HR/9; ROUND(x, 1) for innings, velocity and every rate. Rates are percentages from 0 to 100.
- Write answers for a baseball audience, not a technical one. When the answer is a single number, give it in one sentence that names the team, the stat and the period. For comparisons, rankings, splits and trends, lead with the main takeaway, then walk through the numbers that matter and what they mean for the staff, and show a chart when it makes the comparison easier to see. Don't explain how the answer was produced: no list of filters, no field = value pairs, no column, table or metric view names, and no description of the result set such as "the single result returned" or "based on the result shown".
- If someone asks for the "best" or "worst" pitcher without saying how to measure it, ask which stat they mean (ERA, K%, whiff rate or another stat) before answering."""


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------

SWINGS = "('S','W','T','F','L','M','O','X','D','E')"
WHIFFS = "('S','W','M')"


def benchmarks(s, team, season):
    """(id, [phrasings], trusted SQL). Each phrasing becomes its own benchmark entry."""
    tid, short, park = team["id"], team["short_name"], team["home_park"]
    reg = f"g.gm_typ = 'R' AND g.ssn = {season}"
    era = "ROUND(9 * SUM(l.er) / (SUM(l.outs) / 3.0), 2)"
    return [
        ("B01", [
            f"What was the {short}' team ERA this season?",
            f"{short} staff ERA {season}",
            "How did our pitching staff do by ERA this year?",
        ], f"""SELECT {era} AS team_era
FROM {s}.pgl l
JOIN {s}.gm g ON l.gm_pk = g.gm_pk
WHERE l.tm_id = {tid} AND {reg}"""),

        ("B02", [
            f"What was the {short} bullpen ERA in one-run games?",
            "How did our relievers do in games decided by one run?",
            "Bullpen ERA in one-run games this season",
        ], f"""SELECT {era} AS bullpen_era
FROM {s}.pgl l
JOIN {s}.gm g ON l.gm_pk = g.gm_pk
WHERE l.tm_id = {tid} AND {reg}
  AND l.gs = 0
  AND ABS(g.hm_r - g.aw_r) = 1"""),

        ("B03", [
            f"Top 5 {short} relievers by strikeout rate in August, minimum 25 batters faced",
            "Which of our relievers had the best K% in August? Min 25 batters faced, top 5",
        ], f"""SELECT p.nm_full AS pitcher,
       ROUND(100.0 * SUM(l.so) / SUM(l.bf), 1) AS k_pct
FROM {s}.pgl l
JOIN {s}.gm g ON l.gm_pk = g.gm_pk
JOIN {s}.plyr p ON l.p_id = p.p_id
WHERE l.tm_id = {tid} AND {reg}
  AND l.gs = 0
  AND MONTH(g.gm_dt) = 8
GROUP BY p.nm_full
HAVING SUM(l.bf) >= 25
ORDER BY k_pct DESC, pitcher
LIMIT 5"""),

        ("B04", [
            f"{short} ERA at home vs on the road this season",
            f"How did our pitchers do at {park} compared to away games?",
            f"Home and road ERA split for the {short} staff",
        ], f"""SELECT CASE WHEN g.hm_tm_id = {tid} THEN 'Home' ELSE 'Road' END AS home_road,
       {era} AS era
FROM {s}.pgl l
JOIN {s}.gm g ON l.gm_pk = g.gm_pk
WHERE l.tm_id = {tid} AND {reg}
GROUP BY 1
ORDER BY 1"""),

        ("B05", [
            f"Which {short} pitcher had the highest average four-seam fastball velocity? Minimum 200 four-seamers",
            "Who threw the hardest four-seamer on our staff on average (at least 200 thrown)?",
        ], f"""SELECT p.nm_full AS pitcher,
       ROUND(AVG(x.spd), 1) AS avg_ff_velo
FROM {s}.pch x
JOIN {s}.gm g ON x.gm_pk = g.gm_pk
JOIN {s}.plyr p ON x.p_id = p.p_id
WHERE x.fld_tm = {tid} AND {reg}
  AND x.pt_cd = 'FF'
GROUP BY p.nm_full
HAVING COUNT(*) >= 200
ORDER BY avg_ff_velo DESC
LIMIT 1"""),

        ("B06", [
            f"What was the whiff rate on sweepers thrown by {short} pitchers this season?",
            "Our staff's swing-and-miss rate on sweepers",
            f"{short} sweeper whiff %",
        ], f"""SELECT ROUND(100.0 * SUM(CASE WHEN x.cd IN {WHIFFS} THEN 1 ELSE 0 END)
                   / SUM(CASE WHEN x.cd IN {SWINGS} THEN 1 ELSE 0 END), 1) AS whiff_pct
FROM {s}.pch x
JOIN {s}.gm g ON x.gm_pk = g.gm_pk
WHERE x.fld_tm = {tid} AND {reg}
  AND x.pt_cd = 'ST'"""),

        ("B07", [
            f"What was the hard-hit rate allowed by {short} pitchers at {park}?",
            "How often did opponents hit the ball 95+ mph off us at home?",
            f"Hard-hit % against our pitchers at {park} this season",
        ], f"""SELECT ROUND(100.0 * SUM(CASE WHEN x.ev >= 95 THEN 1 ELSE 0 END) / COUNT(*), 1) AS hard_hit_pct
FROM {s}.pch x
JOIN {s}.gm g ON x.gm_pk = g.gm_pk
WHERE x.fld_tm = {tid} AND {reg}
  AND g.hm_tm_id = {tid}
  AND x.cd IN ('X','D','E')
  AND x.ev IS NOT NULL"""),

        ("B08", [
            f"How many strikeouts did {short} pitchers get on sweepers this season?",
            "Number of punchouts our staff recorded with a sweeper",
        ], f"""SELECT COUNT(*) AS sweeper_strikeouts
FROM {s}.pch x
JOIN {s}.gm g ON x.gm_pk = g.gm_pk
WHERE x.fld_tm = {tid} AND {reg}
  AND x.pt_cd = 'ST'
  AND x.is_lst = true
  AND x.ev_typ IN ('strikeout', 'strikeout_double_play')"""),

        ("B09", [
            f"Which {short} starter had the most quality starts this season?",
            "Who led our rotation in quality starts?",
        ], f"""SELECT p.nm_full AS pitcher,
       COUNT(*) AS quality_starts
FROM {s}.pgl l
JOIN {s}.gm g ON l.gm_pk = g.gm_pk
JOIN {s}.plyr p ON l.p_id = p.p_id
WHERE l.tm_id = {tid} AND {reg}
  AND l.gs = 1 AND l.outs >= 18 AND l.er <= 3
GROUP BY p.nm_full
ORDER BY quality_starts DESC, pitcher
LIMIT 1"""),

        ("B10", [
            f"What was the {short}' record in one-run games this season?",
            "How did we do in one-run games? Wins and losses",
        ], f"""SELECT SUM(CASE WHEN (g.hm_tm_id = {tid} AND g.hm_r > g.aw_r)
                  OR (g.aw_tm_id = {tid} AND g.aw_r > g.hm_r) THEN 1 ELSE 0 END) AS wins,
       SUM(CASE WHEN (g.hm_tm_id = {tid} AND g.hm_r < g.aw_r)
                  OR (g.aw_tm_id = {tid} AND g.aw_r < g.hm_r) THEN 1 ELSE 0 END) AS losses
FROM {s}.gm g
WHERE {reg}
  AND {tid} IN (g.hm_tm_id, g.aw_tm_id)
  AND ABS(g.hm_r - g.aw_r) = 1"""),
    ]


# ---------------------------------------------------------------------------
# Round-by-round Genie agents
# ---------------------------------------------------------------------------

BASE_TABLES = ["gm", "tm", "plyr", "pgl", "pch"]

# Lever 1: columns hidden from Genie in rounds 1 to 3
HIDDEN_COLUMNS = {
    "gm": ["_ld_ts", "_src", "_btch_id", "dur_min", "att"],
    "tm": ["_ld_ts", "_src", "_btch_id"],
    "plyr": ["_ld_ts", "_src", "_btch_id"],
    "pgl": ["_ld_ts", "_src", "_btch_id", "ip"],
    "pch": ["_ld_ts", "_src", "_btch_id", "end_spd", "spn_dir", "brk_ang", "brk_len", "pfx_x", "pfx_z",
            "vx0", "vy0", "vz0", "ax", "ay", "az", "sz_top", "sz_bot", "plt_tm", "typ_conf", "hrd", "loc", "play_id"],
}

# Lever 2: agent-only synonyms
SYNONYMS = {
    ("pgl", "so"): ["strikeouts", "Ks", "punchouts"],
    ("pgl", "bf"): ["batters faced", "TBF"],
    ("pgl", "gs"): ["starter", "rotation", "start"],
    ("pch", "spd"): ["velocity", "velo", "mph"],
    ("pch", "pt_cd"): ["pitch type", "pitch"],
    ("pch", "ev"): ["exit velocity", "exit velo", "EV"],
    ("pch", "fld_tm"): ["pitching team"],
    ("gm", "vnu_nm"): ["ballpark", "stadium", "park"],
}

# Lever 2: example values and a value dictionary (entity matching) on the label columns people
# name or filter by. Kept to categorical strings on purpose: IDs, counts, measures, dates and
# free text get nothing, because sampling them adds context without helping Genie match a value.
ENTITY_MATCHING = {
    ("tm", "tm_nm"), ("tm", "tm_abbr"), ("tm", "tm_shrt"), ("tm", "lg_nm"), ("tm", "div_nm"),
    ("plyr", "nm_full"), ("plyr", "pos_cd"), ("plyr", "thr"), ("plyr", "bat"),
    ("gm", "gm_typ"), ("gm", "vnu_nm"), ("gm", "dn"), ("gm", "wx_cond"),
    ("pch", "pt_cd"), ("pch", "cd"), ("pch", "ev_typ"), ("pch", "traj"), ("pch", "half"), ("pch", "p_thr"), ("pch", "b_sd"),
}

# The same for the label dimensions of the metric views (rounds 2 and 3)
MV_ENTITY_MATCHING = {
    "pitching_metrics": ["Game Type", "Team", "Team Abbreviation", "Role", "Home or Road", "Venue", "Pitcher", "Game Result"],
    "pitch_metrics": ["Game Type", "Pitching Team", "Pitch Type", "Pitch Type Code", "Home or Road", "Zone",
                      "Pitcher", "Batter", "Batter Side", "Pitcher Throws", "Batted Ball Type", "Venue"],
}

# Lever 4 (rounds 2 and 3): the words people use for a regular season
REGULAR_SEASON_SYNONYMS = ["this season", "this year", "regular season", "season"]

# Lever 3: the UC key relationships, declared in the agent with clear aliases.
# Genie derives joins from UC keys when an agent is created; declaring them here keeps
# the set exact on every update (left table, column, right table, column, right alias).
JOINS = [
    ("gm", "hm_tm_id", "tm", "tm_id", "home_team"),
    ("gm", "aw_tm_id", "tm", "tm_id", "away_team"),
    ("pgl", "gm_pk", "gm", "gm_pk", "gm"),
    ("pgl", "p_id", "plyr", "p_id", "plyr"),
    ("pgl", "tm_id", "tm", "tm_id", "tm"),
    ("pch", "gm_pk", "gm", "gm_pk", "gm"),
    ("pch", "p_id", "plyr", "p_id", "pitcher"),
    ("pch", "b_id", "plyr", "p_id", "batter"),
    ("pch", "fld_tm", "tm", "tm_id", "pitching_team"),
]

def sample_questions(team):
    return [
        "What was our bullpen ERA in one-run games?",
        f"What does {team['pitcher']}'s arsenal look like?",
        "Which of our starters pitched deepest into games?",
        f"Give me {team['pitcher']}'s season line",
    ]

# The fields that describe the team, and their widget labels. The bundle's team variable has
# the same fields; pitcher is a pitcher on the team, used in examples.
TEAM_WIDGETS = {"id": "Team ID (MLB Stats API)", "name": "Team name", "short_name": "Team short name",
                "abbreviation": "Team abbreviation", "home_park": "Home ballpark", "pitcher": "Example pitcher"}


def team_widgets(dbutils):
    """Create the team widgets and return the team they describe.

    They start blank, so a notebook run by hand can't quietly use the wrong team. The jobs
    fill them in from the bundle's team variable.
    """
    for k, label in TEAM_WIDGETS.items():
        dbutils.widgets.text(f"team_{k}", "", label)
    team = {k: dbutils.widgets.get(f"team_{k}").strip() for k in TEAM_WIDGETS}
    blank = [f"team_{k}" for k, v in team.items() if not v]
    if blank:
        raise Exception(f"Set the team widgets first: {', '.join(blank)}. The jobs fill them in from the bundle's team variable.")
    team["id"] = int(team["id"])
    return team


ROUNDS = {
    0: ("Pitching R0: raw tables", "Round 0. Six raw tables, no comments, keys, examples or instructions."),
    1: ("Pitching R1: data levers", "Round 1. Levers 1 to 3: noise table removed, columns hidden, UC comments, synonyms, value matching, keys and joins."),
    2: ("Pitching R2: metric views", "Round 2. Round 1 plus lever 4: the pitching_metrics and pitch_metrics metric views and a Regular season filter."),
    3: ("Pitching R3: examples and rules", "Round 3. Round 2 plus levers 5 and 6: example SQL, a trusted function and general instructions."),
}


def agent_title(rnd, team):
    return f"{team['short_name']} {ROUNDS[rnd][0]}"


def _id(*parts):
    """Stable 32-hex ID, so rebuilding an agent keeps the same IDs."""
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()


def _lines(text):
    return text.splitlines(keepends=True)


def agent_config(rnd, sp, team, season):
    """serialized_space for round rnd. sp is the plain catalog.schema the agent reads; team is a dict with the TEAM_WIDGETS fields."""
    tables = BASE_TABLES + (["ld_log"] if rnd == 0 else [])
    data_sources = {"tables": []}
    for t in sorted(tables):
        entry = {"identifier": f"{sp}.{t}"}
        if rnd >= 1:
            configs = {}
            for col in HIDDEN_COLUMNS.get(t, []):
                configs.setdefault(col, {})["exclude"] = True
            for (tt, col), syn in SYNONYMS.items():
                if tt == t:
                    configs.setdefault(col, {})["synonyms"] = syn
            for tt, col in ENTITY_MATCHING:
                if tt == t:
                    configs.setdefault(col, {}).update(enable_format_assistance=True, enable_entity_matching=True)
            entry["column_configs"] = [{"column_name": c, **v} for c, v in sorted(configs.items())]
        data_sources["tables"].append(entry)
    if rnd >= 2:
        data_sources["metric_views"] = [{
            "identifier": f"{sp}.{v}",
            "column_configs": [{"column_name": c, "enable_format_assistance": True, "enable_entity_matching": True}
                               for c in sorted(MV_ENTITY_MATCHING[v])],
        } for v in sorted(MV_ENTITY_MATCHING)]

    space = {"version": 2, "data_sources": data_sources}
    instr = {}
    if rnd >= 1:
        instr["join_specs"] = sorted([{
            "id": _id("join", lt, lc, rt, rc),
            "left": {"identifier": f"{sp}.{lt}", "alias": lt},
            "right": {"identifier": f"{sp}.{rt}", "alias": alias},
            "sql": [f"`{lt}`.`{lc}` = `{alias}`.`{rc}`", "--rt=FROM_RELATIONSHIP_TYPE_MANY_TO_ONE--"],
        } for lt, lc, rt, rc, alias in JOINS], key=lambda j: j["id"])
    if rnd >= 2:
        # Lever 4 alternative from the run sheet: a named Filter as a SQL expression.
        # It says what "this season" means; R3's instructions make it the default.
        instr["sql_snippets"] = {"filters": [{
            "id": _id("filter", "regular season"),
            "display_name": "Regular season",
            "sql": ["`gm`.`gm_typ` = 'R'"],
            "synonyms": REGULAR_SEASON_SYNONYMS + [f"{season} season"],
            "comment": ["Regular season games only, no spring training. In the metric views the same filter is Game Type = 'Regular season'."],
        }]}
    if rnd >= 3:
        exs = []
        # Example and sample question IDs come from their position, not their text, which
        # names the team: the bundle fills the team in after the IDs are made.
        for i, (title, sql) in enumerate(examples(sp, team, season)):
            ex = {"id": _id("example", i), "question": [title], "sql": _lines(sql)}
            params = [{"name": n, "type_hint": th, "description": [d]}
                      for n, (th, d) in example_parameters(team).items() if f":{n}" in sql]
            if params:
                ex["parameters"] = params
            exs.append(ex)
        instr["example_question_sqls"] = sorted(exs, key=lambda e: e["id"])
        instr["text_instructions"] = [{"id": _id("rules"), "content": _lines(instructions(season, team))}]
        instr["sql_functions"] = [{"id": _id("function", "pitcher_season_line"),
                                   "identifier": f"{sp}.pitcher_season_line"}]
        space["config"] = {"sample_questions": sorted(
            [{"id": _id("sample", i), "question": [q]} for i, q in enumerate(sample_questions(team))],
            key=lambda x: x["id"])}
    if instr:
        space["instructions"] = instr

    # IDs come from the benchmark ID and phrasing number, not the text, so a phrasing that
    # names the season gets the same ID whether the season is filled in here or by the bundle.
    space["benchmarks"] = {"questions": sorted([
        {"id": _id("bench", rnd, bid, i), "question": [p], "answer": [{"format": "SQL", "content": _lines(sql)}]}
        for bid, phrasings, sql in benchmarks(sp, team, season) for i, p in enumerate(phrasings)
    ], key=lambda b: b["id"])}
    return space


# ---------------------------------------------------------------------------
# Syncing agents and running benchmarks. The bundle creates the agents.
# ---------------------------------------------------------------------------

def sync_agent(w, space_id, rnd, sp, team, season):
    """Apply the round's config to an agent the bundle deployed.

    Creating an agent also adds joins derived from the UC keys, which duplicate the declared
    ones, and a bundle deploy doesn't reliably remove them. Applying the same config again
    leaves exactly what's defined here, which is also exactly what the bundle deployed.
    """
    space = agent_config(rnd, sp, team, season)
    w.api_client.do("PATCH", f"/api/2.0/genie/spaces/{space_id}", body={"serialized_space": json.dumps(space)})
    print(f"Synced {agent_title(rnd, team)}\n  {w.config.host.rstrip('/')}/genie/rooms/{space_id}")


def start_benchmark(w, space_id):
    run = w.api_client.do("POST", f"/api/2.0/genie/spaces/{space_id}/eval-runs", body={})
    print(f"Started benchmark run {run.get('eval_run_id')}. Results appear on the agent's Benchmarks tab.")


def describe_agent_settings(rnd, team):
    """Readable summary of the agent-side settings a round adds."""
    lines = []
    if rnd == 1:
        lines.append("Removed: ld_log")
        lines.append("Hidden columns:")
        lines += [f"  {t}: {', '.join(cols)}" for t, cols in HIDDEN_COLUMNS.items()]
        lines.append("Synonyms:")
        lines += [f"  {t}.{c}: {', '.join(s)}" for (t, c), s in SYNONYMS.items()]
        lines.append("Example values and value dictionary (label columns only):")
        lines += [f"  {t}: {', '.join(c for tt, c in sorted(ENTITY_MATCHING) if tt == t)}" for t in BASE_TABLES
                  if any(tt == t for tt, _ in ENTITY_MATCHING)]
        lines.append("Joins (many to one):")
        lines += [f"  {lt}.{lc} = {alias}.{rc}   ({alias} is {rt})" for lt, lc, rt, rc, alias in JOINS]
    elif rnd == 2:
        lines.append("Added sources: pitching_metrics, pitch_metrics")
        lines.append("Added SQL expression (Filter): Regular season = gm.gm_typ = 'R', synonyms: " + ", ".join(REGULAR_SEASON_SYNONYMS))
        lines.append("Example values and value dictionary on the label dimensions:")
        lines += [f"  {v}: {', '.join(cols)}" for v, cols in MV_ENTITY_MATCHING.items()]
    elif rnd == 3:
        lines.append("Example SQL questions:")
        lines += [f"  {title}" for title, _ in examples("s", team, 0)]
        lines.append("Parameter: pitcher_name (STRING), part of the pitcher name")
        lines.append("Added source: pitcher_season_line (function)")
        lines.append("Landing-page questions:")
        lines += [f"  {q}" for q in sample_questions(team)]
    return "\n".join(lines)

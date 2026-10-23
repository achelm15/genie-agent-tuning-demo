# Databricks notebook source
# MAGIC %md
# MAGIC # 01: Load the data
# MAGIC
# MAGIC This notebook pulls one season of games for one team from the public MLB Stats API and writes them as six raw tables, with short cryptic column names, no comments and no keys, which is how a lot of tables look the day they land. The team comes from the **team** widgets, which the `setup_data` job fills in from the bundle's `team` variable. If you run the notebook by hand, set them first.
# MAGIC
# MAGIC The same tables go to two schemas:
# MAGIC
# MAGIC | Schema | Read by | Tuned |
# MAGIC |---|---|---|
# MAGIC | `genie_pitching_raw` | R0 | No |
# MAGIC | `genie_pitching_tuned` | R1, R2 and R3 | Yes, by notebooks 03 to 05 |
# MAGIC
# MAGIC The comments and keys from round 1 live on the tables themselves, so if every agent read the same tables, tuning round 1 would also improve R0. Keeping a raw copy keeps R0 a fair baseline.
# MAGIC
# MAGIC ### How the pieces fit
# MAGIC Some levers live in Unity Catalog and some live in the agent, and each kind gets set in a different place:
# MAGIC
# MAGIC | Where | Levers | Set by |
# MAGIC |---|---|---|
# MAGIC | Unity Catalog | Comments, keys, the two metric views and the `pitcher_season_line` function | Notebooks 01, 03, 04 and 05, which the `setup_data` job runs in that order |
# MAGIC | The agent | Hidden columns, synonyms, value matching, joins, sources, the Regular season filter, example SQL, instructions and the benchmark | The bundle, from the files in `resources/agents/` |
# MAGIC
# MAGIC Steps for a new workspace:
# MAGIC 1. `databricks bundle deploy -t prod` creates the jobs. The agents fail on this first deploy, because Genie won't create an agent on tables that don't exist yet.
# MAGIC 2. `databricks bundle run setup_data -t prod` loads the data and makes the Unity Catalog changes, in about 5 minutes.
# MAGIC 3. `databricks bundle deploy -t prod` again creates the agents.
# MAGIC 4. `databricks bundle run sync_agents -t prod` cleans up the agents' joins and starts their benchmark runs.
# MAGIC
# MAGIC Notebooks 02 to 05 walk through one round each. Reading them doesn't change anything, and the cells that write to Unity Catalog say so in the line above them.
# MAGIC
# MAGIC ### Before you run it
# MAGIC - **Compute:** serverless, or a cluster on DBR 17.3 or later. The notebook calls `statsapi.mlb.com`, so the compute needs outbound internet access. If your workspace restricts serverless egress, allow that host or use a classic cluster that can reach it.
# MAGIC - **Permissions:** `USE CATALOG` and `CREATE SCHEMA` on the catalog.
# MAGIC - **Size:** about 193 games, 1,800 pitcher lines and 57,000 pitches per schema. The load takes about a minute.
# MAGIC
# MAGIC Running this notebook again drops and rebuilds both schemas' tables, which also removes the comments, keys, metric views and function from notebooks 03 to 05. Run the `setup_data` job instead if you want everything rebuilt in one go. The agents keep their benchmark history either way.
# COMMAND ----------
# MAGIC %md
# MAGIC ## Settings
# MAGIC Use the same values in every notebook. When a job runs the notebook, the values come from the bundle variables.
# COMMAND ----------
dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("raw_schema", "genie_pitching_raw", "Raw schema (round 0)")
dbutils.widgets.text("tuned_schema", "genie_pitching_tuned", "Tuned schema (rounds 1 to 3)")
dbutils.widgets.text("season", "2026", "Season")

CATALOG = dbutils.widgets.get("catalog").strip()
RAW = dbutils.widgets.get("raw_schema").strip()
TUNED = dbutils.widgets.get("tuned_schema").strip()
SEASON = int(dbutils.widgets.get("season"))

import importlib
import genie_demo as g
importlib.reload(g)
TEAM = g.team_widgets(dbutils)

print(f"Raw: {CATALOG}.{RAW}   Tuned: {CATALOG}.{TUNED}   Season: {SEASON}   Team: {TEAM['name']}")
# COMMAND ----------

# MAGIC %md
# MAGIC ## Load the data
# MAGIC
# MAGIC This pulls every final game the team played that season, spring training included, from the MLB Stats API live game feed, and writes the same six tables to both schemas:
# MAGIC
# MAGIC | Table | What it holds | Grain |
# MAGIC |---|---|---|
# MAGIC | `gm` | Games: date, type, teams, final score, venue, weather | One row per game |
# MAGIC | `tm` | MLB teams | One row per team |
# MAGIC | `plyr` | Players on either roster | One row per player |
# MAGIC | `pgl` | Pitcher game lines from the box score (IP, ER, K, BB, BF, starts) | One row per pitcher per game |
# MAGIC | `pch` | Pitch by pitch: type, velocity, spin, movement, location, call, exit velocity | One row per pitch |
# MAGIC | `ld_log` | A load audit log, nothing to do with baseball | One row per API call |
# MAGIC
# MAGIC The data has a few traps on purpose, and each later round fixes one or more of them:
# MAGIC - Spring training is mixed in with the regular season, and only `gm.gm_typ` (`S` or `R`) tells them apart.
# MAGIC - `pgl.ip` is innings in baseball notation, where 5.2 means 5 and 2/3 innings, so `SUM(ip)` is wrong. The column to use is `outs`.
# MAGIC - Teams are stored as numeric IDs, and nothing in `pgl` or `pch` names the team.
# MAGIC - Both teams are in every table, so opponent pitchers show up in `pgl` and `pch` too.
# MAGIC - Pitch types and results are codes: `ST` is a sweeper, `S` is a swinging strike, `X` is a ball in play.
# MAGIC - The at-bat result repeats on every pitch. `pch.ev_typ` is `strikeout` on all five pitches of a five-pitch strikeout, so counting rows overcounts by about 4x.
# MAGIC - The only thing that separates starters from relievers is `pgl.gs`.
# MAGIC
# MAGIC The next cell drops and rebuilds both schemas' tables.

# COMMAND ----------
data = g.load_season(SEASON, TEAM["id"])

for schema in (RAW, TUNED):
    s = f"`{CATALOG}`.`{schema}`"
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {s}")
    g.drop_demo_objects(spark, s)
    g.write_tables(spark, data, s)
# COMMAND ----------
# MAGIC %md
# MAGIC Here's what Genie sees in round 0. Column names like `cd`, `gs` and `ip` don't give it much to go on.
# COMMAND ----------
display(spark.sql(f"SELECT * FROM `{CATALOG}`.`{RAW}`.pgl LIMIT 10"))
display(spark.sql(f"SELECT gm_pk, ab_idx, pch_seq, fld_tm, p_id, pt_cd, cd, spd, spn, ev, is_lst, ev_typ FROM `{CATALOG}`.`{RAW}`.pch LIMIT 10"))
# COMMAND ----------
# MAGIC %md
# MAGIC Next: **02_Round0_Raw_Agent**.

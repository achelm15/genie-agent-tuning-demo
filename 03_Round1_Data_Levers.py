# Databricks notebook source
# MAGIC %md
# MAGIC # Round 1: levers 1 to 3, the data itself
# MAGIC Round 1 is all metadata about the data. There's no prompt writing yet.
# MAGIC
# MAGIC | Lever | Where it lives | What R1 adds |
# MAGIC |---|---|---|
# MAGIC | 1. Keep the agent focused | Agent | `ld_log` removed, and 37 noise columns hidden, including `ip` |
# MAGIC | 2. Comment every table and column | Unity Catalog and the agent | A comment on every table and column in Unity Catalog, plus synonyms and value matching in the agent |
# MAGIC | 3. Tell Genie how the tables join | Unity Catalog and the agent | Primary and foreign keys, with the same 9 relationships declared as the agent's joins |
# MAGIC
# MAGIC This notebook makes the Unity Catalog changes, on the tuned schema only, and the `setup_data` job runs it right after notebook 01. Because the changes live in Unity Catalog, Genie One, Genie Code and any other agent on these tables pick them up too. The agent settings come from the bundle.

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("tuned_schema", "genie_pitching_tuned", "Tuned schema (rounds 1 to 3)")
dbutils.widgets.text("season", "2026", "Season")

import importlib
import genie_demo as g

importlib.reload(g)

CATALOG = dbutils.widgets.get("catalog").strip()
TUNED = dbutils.widgets.get("tuned_schema").strip()
SEASON = int(dbutils.widgets.get("season"))
TEAM = g.team_widgets(dbutils)
S = f"`{CATALOG}`.`{TUNED}`"  # quoted form for SQL run here
SP = f"{CATALOG}.{TUNED}"      # plain form for metric view YAML and the agent

missing = [o for o in ['gm', 'tm', 'plyr', 'pgl', 'pch'] if not spark.catalog.tableExists(f"{SP}.{o}")]
if missing:
    raise Exception(f"Missing in {SP}: {missing}. Run the earlier notebooks first, with the same widgets.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Lever 2: comment every table and column
# MAGIC The comments say what you'd tell a new analyst on their first day. These ones do the most work:
# MAGIC - `pgl.ip`: "Innings pitched in baseball notation, where 5.2 means 5 and 2/3 innings. Never sum or average this column."
# MAGIC - `pch.pt_cd` spells out every pitch code, for example `ST` = sweeper.
# MAGIC - `pch.ev_typ`: "Repeated on every pitch of the plate appearance, so count it only where is_lst = true."
# MAGIC - `gm.gm_typ`: "Game type: R = regular season, S = spring training." The comment only says what the codes mean. Which games count as "the season" is a business rule, so that goes in the round 3 instructions.
# MAGIC
# MAGIC The full text is in `TABLE_COMMENTS` and `COLUMN_COMMENTS` in `genie_demo.py`, and the comments that name the team are filled in from the **team** widgets.
# MAGIC
# MAGIC The next cell writes a comment on every table and column in the tuned schema.

# COMMAND ----------

g.apply_comments(spark, S, TEAM)
display(spark.sql(f"DESCRIBE TABLE {S}.pgl"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Lever 3: primary and foreign keys
# MAGIC Genie reads the keys as join relationships, and so do other tools. They're informational and not enforced, so they're only as good as the data behind them.
# MAGIC
# MAGIC The next cell adds the keys.

# COMMAND ----------

g.apply_keys(spark, S)

# COMMAND ----------

# MAGIC %md
# MAGIC ## What goes in the agent
# MAGIC Hidden columns, synonyms, value matching and joins are agent settings, not Unity Catalog ones. The bundle deploys them from `resources/agents/r1_data_levers.genie_space.yml`, which `scripts/build_agent_resources.py` generates from `agent_config()` in `genie_demo.py`.
# MAGIC
# MAGIC - Example values and a value dictionary are only on the label columns people filter by, like team names, pitch types and result codes, and not on IDs, measures or dates.
# MAGIC - A hidden column drops out of Genie's context but stays in Unity Catalog for anyone with access.
# MAGIC - The joins are the same 9 relationships as the keys. When Genie creates an agent it also adds joins from the keys, which would double them up, so the `sync_agents` job applies the config again to leave exactly these 9.
# MAGIC
# MAGIC Open R1 in Genie to see these in its configuration. This prints what round 1 adds:

# COMMAND ----------

print(g.describe_agent_settings(1, TEAM))

# COMMAND ----------

# MAGIC %md
# MAGIC Next: **04_Round2_Metric_Views**.

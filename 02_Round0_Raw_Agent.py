# Databricks notebook source
# MAGIC %md
# MAGIC # Round 0: the raw agent
# MAGIC R0 is the baseline, an agent on the six raw tables exactly as they landed, with no comments, keys, synonyms, joins, examples or instructions, and with `ld_log`, a load log that has nothing to do with baseball, still attached. It reads the raw schema, which nothing tunes, so its score stays the same no matter what the later rounds change.
# MAGIC
# MAGIC Nothing in this round touches Unity Catalog, so the cells here only read data.
# MAGIC
# MAGIC What R0 usually gets wrong:
# MAGIC
# MAGIC | Question | Why it goes wrong |
# MAGIC |---|---|
# MAGIC | Team ERA | It sums `ip`, which is in baseball notation, and it includes spring training, so the ERA comes out wrong |
# MAGIC | Sweeper strikeouts | It doesn't know `ST` means sweeper, and it counts every pitch of each at-bat, so the count comes out about 4x too high |
# MAGIC | Bullpen ERA in one-run games | Nothing tells it that `gs = 0` means a reliever |
# MAGIC | Anything that names the team | Teams are stored as IDs, and nothing maps the ID to the name |
# MAGIC
# MAGIC The bundle builds R0 from `resources/agents/r0_raw_tables.genie_space.yml`. You'll find it in the bundle's `workspace_folder`, and its **Benchmarks** tab shows the score from the last `sync_agents` run.

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("raw_schema", "genie_pitching_raw", "Raw schema (round 0)")
dbutils.widgets.text("season", "2026", "Season")

import importlib
import genie_demo as g

importlib.reload(g)

CATALOG = dbutils.widgets.get("catalog").strip()
RAW = dbutils.widgets.get("raw_schema").strip()
SEASON = int(dbutils.widgets.get("season"))
TEAM = g.team_widgets(dbutils)
S = f"`{CATALOG}`.`{RAW}`"  # quoted form for SQL run here
SP = f"{CATALOG}.{RAW}"      # plain form for metric view YAML and the agent

# COMMAND ----------

# MAGIC %md
# MAGIC ## What Genie has to go on
# MAGIC These are the only tables R0 can see. Look at a few of the column names, since that's all it has to work with.

# COMMAND ----------

display(spark.sql(f"SELECT * FROM {S}.pgl LIMIT 5"))
display(spark.sql(f"DESCRIBE TABLE {S}.pgl"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## The benchmark
# MAGIC All four agents get the same 25 entries, 10 questions each asked 2 or 3 different ways, with trusted SQL as the expected answer. Asking a question a few ways checks whether Genie understands it rather than matching on the wording. The SQL is in `benchmarks()` in `genie_demo.py`.

# COMMAND ----------

for bid, phrasings, _ in g.benchmarks(SP, TEAM, SEASON):
    print(bid)
    for p in phrasings:
        print(f"  {p}")

# COMMAND ----------

# MAGIC %md
# MAGIC Next: **03_Round1_Data_Levers**.

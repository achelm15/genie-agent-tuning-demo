# Databricks notebook source
# MAGIC %md
# MAGIC # 06: Sync the agents and start benchmarks
# MAGIC The `sync_agents` job runs this notebook after a deploy, and it does two things:
# MAGIC - It applies each round's config to its agent again. When Genie creates an agent it also adds joins from the Unity Catalog keys, which doubles up the declared joins, and a bundle deploy doesn't reliably remove them. Applying the config again leaves exactly what the bundle defines.
# MAGIC - It starts a Chat mode benchmark run on each agent, unless **run_benchmark** is `no`. The runs happen in the background, and the results stay on each agent's **Benchmarks** tab.
# MAGIC
# MAGIC The job fills in the agent IDs from the bundle. To run the notebook by hand, paste the IDs into the widgets.

# COMMAND ----------

dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("raw_schema", "genie_pitching_raw", "Raw schema (round 0)")
dbutils.widgets.text("tuned_schema", "genie_pitching_tuned", "Tuned schema (rounds 1 to 3)")
dbutils.widgets.text("season", "2026", "Season")
for rnd in range(4):
    dbutils.widgets.text(f"r{rnd}_space_id", "", f"R{rnd} agent ID")
dbutils.widgets.dropdown("run_benchmark", "yes", ["yes", "no"], "Start benchmark runs")

import importlib
import genie_demo as g
from databricks.sdk import WorkspaceClient

importlib.reload(g)
TEAM = g.team_widgets(dbutils)
w = WorkspaceClient()

CATALOG = dbutils.widgets.get("catalog").strip()
RAW = dbutils.widgets.get("raw_schema").strip()
TUNED = dbutils.widgets.get("tuned_schema").strip()
SEASON = int(dbutils.widgets.get("season"))

# COMMAND ----------

for rnd in range(4):
    space_id = dbutils.widgets.get(f"r{rnd}_space_id").strip()
    if not space_id:
        raise Exception(f"r{rnd}_space_id is blank. Run this notebook through the sync_agents job, or paste the agent IDs.")
    sp = f"{CATALOG}.{RAW if rnd == 0 else TUNED}"
    g.sync_agent(w, space_id, rnd, sp, TEAM, SEASON)
    if dbutils.widgets.get("run_benchmark") == "yes":
        g.start_benchmark(w, space_id)

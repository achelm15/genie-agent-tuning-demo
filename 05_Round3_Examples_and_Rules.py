# Databricks notebook source
# MAGIC %md
# MAGIC # Round 3: levers 5 and 6, example SQL and a few rules
# MAGIC R3 is R2 plus:
# MAGIC
# MAGIC | Lever | Where it lives | What R3 adds |
# MAGIC |---|---|---|
# MAGIC | 5. Example SQL | Agent | Four example questions with trusted SQL, worded the way people ask. One takes a `pitcher_name` parameter, so answers that reuse it show as verified |
# MAGIC | 5. Trusted function | Unity Catalog | `pitcher_season_line`, logic Genie can call but can't see or rewrite. Its answers show as verified, and users need `EXECUTE` on it |
# MAGIC | 6. General instructions | Agent | Seven short rules covering who "we" is, what "this season" means, bullpen versus rotation, using the metric view measures, rounding, writing for a non-technical reader and when to ask a clarifying question |
# MAGIC
# MAGIC The examples teach patterns, and none of them copies a benchmark question, so the benchmark still tests whether Genie generalizes. The function is the only Unity Catalog change in this round, and the `setup_data` job runs this notebook to create it.

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

missing = [o for o in ['gm', 'tm', 'plyr', 'pgl', 'pch', 'pitching_metrics', 'pitch_metrics'] if not spark.catalog.tableExists(f"{SP}.{o}")]
if missing:
    raise Exception(f"Missing in {SP}: {missing}. Run the earlier notebooks first, with the same widgets.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Lever 5: the trusted function
# MAGIC The next cell creates or replaces `pitcher_season_line` and calls it for the example pitcher.

# COMMAND ----------

g.create_function(spark, S, SEASON, TEAM)
display(spark.sql(f"SELECT * FROM {S}.pitcher_season_line('{TEAM['pitcher']}')"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Lever 5: the example SQL
# MAGIC The examples are agent settings, not Unity Catalog objects. This prints each one and runs it once with the example pitcher, to make sure the SQL works on these tables.

# COMMAND ----------

for title, sql in g.examples(SP, TEAM, SEASON):
    print(f"----- {title}\n{sql}\n")

for _, sql in g.examples(SP, TEAM, SEASON):
    spark.sql(sql.replace(":pitcher_name", f"'{TEAM['pitcher']}'")).limit(1).collect()
print("All example queries run.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Lever 6: the general instructions
# MAGIC The list is short on purpose. Everything else already lives in a comment, a key, a metric view or an example, and Genie gets worse at prioritizing as the list gets longer.

# COMMAND ----------

print(g.instructions(SEASON, TEAM))

# COMMAND ----------

# MAGIC %md
# MAGIC ## What goes in the agent
# MAGIC The bundle deploys R3 from `resources/agents/r3_examples_and_rules.genie_space.yml`, which is R2 plus the example SQL, the function, the instructions above and the landing-page questions printed below.

# COMMAND ----------

print(g.describe_agent_settings(3, TEAM))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Compare the four agents
# MAGIC The `sync_agents` job starts a benchmark run on all four agents. When the runs finish, each agent's **Benchmarks** tab has its score.
# MAGIC
# MAGIC ```bash
# MAGIC databricks bundle run sync_agents -t prod
# MAGIC ```

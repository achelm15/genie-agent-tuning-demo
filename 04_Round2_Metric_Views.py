# Databricks notebook source
# MAGIC %md
# MAGIC # Round 2: lever 4, define each KPI once
# MAGIC Round 2 adds two metric views in Unity Catalog, one per fact table, so each KPI has one definition that Genie, Genie One, dashboards and SQL all share:
# MAGIC - `pitching_metrics`, on the box score lines: ERA, WHIP, K%, BB%, innings, quality starts and saves. It covers the regular season only, like MLB's official stats, so its `filter` drops spring training.
# MAGIC - `pitch_metrics`, on the pitch tracking data: velocity, spin, whiff rate, CSW%, chase rate, hard-hit rate and strikeouts.
# MAGIC
# MAGIC Rates are on a 0 to 100 scale, the way people say them. The YAML is in `pitching_metrics_yaml()` and `pitch_metrics_yaml()` in `genie_demo.py`, and the `setup_data` job runs this notebook to create the views.
# MAGIC
# MAGIC R2 is R1 with the two metric views added as sources, plus a Regular season filter written as a SQL expression. The filter tells Genie what "this season" means when someone says it. Making it the default when nobody mentions a period is a business rule, so that's left to the round 3 instructions.

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
# MAGIC The next cell creates or replaces both metric views.

# COMMAND ----------

g.create_metric_views(spark, S, SP, TEAM)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Check the views against the trusted SQL
# MAGIC The first two queries show the views by role and by pitch type. The cell after them compares two numbers with the trusted SQL from the benchmark, team ERA (B01) and sweeper strikeouts (B08), and stops if they don't match.

# COMMAND ----------

display(spark.sql(f"""
SELECT `Role`, MEASURE(`ERA`) AS era, MEASURE(`Innings Pitched`) AS ip, MEASURE(`Strikeout Rate`) AS k_pct
FROM {S}.pitching_metrics
WHERE `Team` = '{TEAM['name']}' AND `Game Type` = 'Regular season' AND `Season` = {SEASON}
GROUP BY ALL
"""))
display(spark.sql(f"""
SELECT `Pitch Type`, MEASURE(`Pitches`) AS pitches, MEASURE(`Avg Velocity`) AS velo,
       MEASURE(`Whiff Rate`) AS whiff_pct, MEASURE(`Strikeouts`) AS strikeouts
FROM {S}.pitch_metrics
WHERE `Pitching Team` = '{TEAM['name']}' AND `Game Type` = 'Regular season' AND `Season` = {SEASON}
GROUP BY ALL
ORDER BY pitches DESC
"""))

# COMMAND ----------

trusted = {bid: sql for bid, _, sql in g.benchmarks(SP, TEAM, SEASON)}
team_filter = f"`Game Type` = 'Regular season' AND `Season` = {SEASON}"
checks = {
    "Team ERA (B01)": (
        spark.sql(f"SELECT MEASURE(`ERA`) FROM {S}.pitching_metrics WHERE `Team` = '{TEAM['name']}' AND {team_filter}").first()[0],
        spark.sql(trusted["B01"]).first()[0]),
    "Sweeper strikeouts (B08)": (
        spark.sql(f"SELECT MEASURE(`Strikeouts`) FROM {S}.pitch_metrics WHERE `Pitching Team` = '{TEAM['name']}' AND `Pitch Type` = 'Sweeper' AND {team_filter}").first()[0],
        spark.sql(trusted["B08"]).first()[0]),
}
for name, (view, sql) in checks.items():
    print(f"{name}: metric view {view}, trusted SQL {sql}")
mismatched = [name for name, (view, sql) in checks.items() if float(view or 0) != float(sql or 0)]
if mismatched:
    raise Exception(f"The metric views don't match the trusted SQL for: {', '.join(mismatched)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## What goes in the agent
# MAGIC The bundle deploys R2 from `resources/agents/r2_metric_views.genie_space.yml`. It's R1 plus the two metric views as sources, value matching on their label dimensions and the Regular season filter. Genie can only add a metric view that already exists, which is why `setup_data` has to run before the first deploy on a new workspace.

# COMMAND ----------

print(g.describe_agent_settings(2, TEAM))

# COMMAND ----------

# MAGIC %md
# MAGIC Next: **05_Round3_Examples_and_Rules**.

# MLB pitching Genie agent: bad to good

A hands-on demo of tuning a Databricks Genie agent. It loads one season of any MLB team's games from the public MLB Stats API into raw tables with cryptic names, then builds four Genie agents, one per tuning round, each scored with the same 25-entry benchmark. Because the agents are pre-built, their benchmarks run ahead of time and a live demo opens finished results instead of waiting on a run.

| File | What it does |
|---|---|
| `01_Load_MLB_Data` | Pulls the season from the MLB Stats API and writes the same six raw tables to two schemas: `genie_pitching_raw` (never tuned) and `genie_pitching_tuned`. Rerun it to reset the data. |
| `02_Round0_Raw_Agent` | Explains the R0 baseline and the benchmark. Changes nothing. |
| `03_Round1_Data_Levers` | Levers 1 to 3: adds UC comments and keys to the tuned schema, and explains R1's hidden columns, synonyms, value matching and joins. |
| `04_Round2_Metric_Views` | Lever 4: creates the two metric views and checks them, and explains what R2 adds. |
| `05_Round3_Examples_and_Rules` | Levers 5 and 6: creates the trusted function, checks the example SQL, and explains R3's examples and general instructions. |
| `06_Sync_Agents` | Applies each agent's config again after a deploy and starts the benchmark runs. |
| `genie_demo.py` | Every definition in one place: the loader, comments, keys, metric views, function, example SQL, rules, benchmarks and agent configs. |
| `databricks.yml`, `resources/` | The bundle: the four agents and two jobs. |
| `scripts/build_agent_resources.py` | Writes `resources/agents/` from the agent configs in `genie_demo.py`. |

A Declarative Automation Bundle deploys the four agents. The notebooks explain each round, and the cells in 01 and 03 to 05 that load the data and make the Unity Catalog changes (comments, keys, metric views, the function) are what the `setup_data` job runs.

Two schemas are needed because Unity Catalog comments and keys live on the tables. If every agent read the same tables, tuning round 1 would also fix the round 0 agent.

## Install

You need the [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install.html) v1.20 or later, `USE CATALOG` and `CREATE SCHEMA` on the catalog, `CAN USE` on a serverless SQL warehouse, permission to create Genie agents, and partner-powered AI features turned on.

The bundle has two targets. `demo` (the default) deploys under your home folder. `prod` deploys to a shared folder you choose: the agents go in the folder and the bundle's files in a `genie-agent-tuning-demo` folder inside it.

| Variable | Default |
|---|---|
| `catalog` | `main` |
| `raw_schema`, `tuned_schema` | `genie_pitching_raw`, `genie_pitching_tuned` |
| `season` | `2026` |
| `team` | None, you have to set it. Fields: `id`, `name`, `short_name`, `abbreviation`, `home_park`, and a `pitcher` on the team used in examples. Set it in `variable-overrides.json`, since `--var` doesn't take it. |
| `warehouse_id` | the warehouse named **Serverless Starter Warehouse** |
| `workspace_folder` | `demo`: `Genie Tuning Demo Agents` in your home folder. `prod`: none, you have to set it. The folder has to exist before the first deploy. |

Pass variables with `--var`, or keep them out of the repo in `.databricks/bundle/<target>/variable-overrides.json` (gitignored):

```json
{
  "catalog": "my_catalog",
  "workspace_folder": "/Workspace/Acme_Onsite",
  "team": {
    "id": 111,
    "name": "Boston Red Sox",
    "short_name": "Red Sox",
    "abbreviation": "BOS",
    "home_park": "Fenway Park",
    "pitcher": "Garrett Crochet"
  }
}
```

Team IDs come from the MLB Stats API (`https://statsapi.mlb.com/api/v1/teams?sportId=1`). The agent titles, comments, instructions, examples and benchmark questions all use the team's names.

The deployment state lives under `workspace_folder`, so a new folder is a new deployment with its own agents. To move one, `databricks bundle destroy` it first, then deploy with the new folder.

Then, from this folder:

```bash
databricks workspace mkdirs /Workspace/Acme_Onsite   # once
databricks bundle deploy -t prod
databricks bundle run setup_data -t prod     # load the data and make the UC changes, about 5 minutes
databricks bundle deploy -t prod             # create any agent the first deploy couldn't
databricks bundle run sync_agents -t prod    # apply each agent's config again and start the benchmark runs
```

On a workspace without the data yet, the first deploy creates the jobs but fails on the agents, because Genie won't create an agent on tables that don't exist. That's why there's a second deploy after `setup_data`. A deploy can also fail with `INTERNAL_ERROR` on one agent; deploy again.

Run `sync_agents` after any deploy that creates an agent. Creating an agent adds joins derived from the UC keys on top of the declared ones, and the job removes the duplicates. Pass `--params run_benchmark=no` to skip the benchmark runs. When the runs finish, open each agent's **Benchmarks** tab to see its score.

To walk through a round, open its notebook from the deployed files (or a Git folder of this repo) with the same **catalog**, schema, **season** and **team** widgets. Each one explains what its round adds and where each setting lives; the cells that change Unity Catalog are marked, and running them again is safe.

### Changing an agent

Edit `genie_demo.py`, then regenerate the agent resources and deploy:

```bash
python3 scripts/build_agent_resources.py
databricks bundle deploy -t prod
```

The generated files use bundle variables for the catalog, schemas and season, so don't edit `resources/agents/` by hand.

## What gets created

| Object | Where | What it is |
|---|---|---|
| `gm`, `tm`, `plyr`, `pgl`, `pch` | both schemas | Raw tables with cryptic names: games, teams, players, pitcher game lines, pitches |
| `ld_log` | both schemas | A load log Genie doesn't need, attached only to R0 |
| `pitching_metrics`, `pitch_metrics` | tuned schema | Metric views (R2 and R3). `pitching_metrics` covers the regular season only; `pitch_metrics` keeps spring training |
| `pitcher_season_line` | tuned schema | A trusted SQL table function (R3) |
| Four Genie agents | the target's `workspace_folder` | One per round, described below |
| `setup_data`, `sync_agents` | Jobs | Load and tune the data; sync the agents and start benchmarks |

A full season is about 190 games, 31 or so of them spring training, with roughly 1,800 pitcher lines and 57,000 pitches per schema. The box score totals for the regular season match MLB's official team numbers.

## The four agents

| Agent | Reads | What's added | Levers |
|---|---|---|---|
| **R0: raw tables** | raw schema | All six tables, nothing else | baseline |
| **R1: data levers** | tuned schema | `ld_log` removed, noise columns and `ip` hidden; UC comments, synonyms, value matching; UC keys, also declared as the agent's joins | 1, 2, 3 |
| **R2: metric views** | tuned schema | R1 plus both metric views | 4 |
| **R3: examples and rules** | tuned schema | R2 plus 4 example queries (one parameterized), the trusted function, 7 general instructions and landing-page questions | 5, 6 |

### The traps, and the lever that fixes each one

| What Genie gets wrong at round 0 | Why | Fixed by |
|---|---|---|
| Team ERA comes out wrong | Sums `ip` (5.2 = 5 and 2/3) and includes spring training | Comments on `ip`, `outs`, `gm_typ` (2); hiding `ip` (1); ERA measure and a regular-season `pitching_metrics` (4); "this season" rule (6) |
| Sweeper strikeouts about 4x too high | Doesn't know `ST` = sweeper; counts every pitch of each at-bat | Comments on `pt_cd`, `ev_typ`, `is_lst` (2); Strikeouts measure (4); example SQL (5) |
| Bullpen ERA in one-run games | Nothing says `gs = 0` is a reliever or how to compute run margin | Comments on `gs`, `hm_r`, `aw_r` (2); Role dimension (4); bullpen example (5) |
| The team's name matches nothing | Teams are stored as IDs | `tm` comment and FK to `tm` (2, 3) |
| Opponent pitchers counted | Both teams are in `pgl` and `pch` | Comments on `tm_id` and `fld_tm` (2) |

### Benchmarks

The questions name whichever team you deploy with, and each answer is trusted SQL in `benchmarks()` in `genie_demo.py`, so the expected answers come from your team's data.

| ID | Question |
|---|---|
| B01 | Team ERA this season |
| B02 | Bullpen ERA in one-run games |
| B03 | Top 5 relievers by K% in August (25+ BF) |
| B04 | ERA at home vs on the road |
| B05 | Hardest average four-seamer (200+ thrown) |
| B06 | Whiff rate on sweepers. Foul tips count as contact, not whiffs |
| B07 | Hard-hit rate allowed at the home park (95+ mph on balls in play) |
| B08 | Strikeouts on sweepers |
| B09 | Most quality starts (6+ innings, 3 or fewer earned runs) |
| B10 | Record in one-run games |

Each question has 2 or 3 phrasings, 25 benchmark entries in total, and every agent gets all 25.

## Reset

- **Data:** `databricks bundle run setup_data -t prod`. It reruns 01 and puts the Unity Catalog changes from 03 to 05 back.
- **Agents:** `databricks bundle deploy -t prod`. It updates the agents in place, so their benchmark history stays.
- **Everything:** `databricks bundle destroy -t prod` removes the agents and jobs. Then drop both schemas:
  ```sql
  DROP SCHEMA IF EXISTS <catalog>.genie_pitching_raw CASCADE;
  DROP SCHEMA IF EXISTS <catalog>.genie_pitching_tuned CASCADE;
  ```

## Notes

- Genie is non-deterministic. Expect scores to move a few points between runs, and do the dry run with the same benchmark set you'll use live.
- An answer with the right number but different rounding or scale (0.273 vs 27.3) can land in **Manual review**. The round 3 rules set the conventions.
- The examples teach patterns and deliberately don't copy any benchmark question, so the benchmark still measures whether Genie generalizes.
- Benchmark runs answer all 25 entries one by one. If they're slow, check the warehouse: a serverless warehouse with max clusters of 2 or more keeps four runs from queueing behind each other.

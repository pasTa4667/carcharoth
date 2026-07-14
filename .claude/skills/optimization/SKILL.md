---
name: optimization-run
description: Run optimization and take the optuna output, appliying it to the config.
disable-model-invocation: true
---

1. First, edit the config/optimize.yaml and change the name of the optimization run, choose a random name, do not ask for a specifc name. Then run the optimization command with default configuration `uv run carcharoth optimize´.

2. After the run is complete, open the generated file under logs/optimize/{prevously_entered_name}.yaml and copy the optimized values into config/config.yaml

3. Delete all generated log files for the run, all log files that container a number in there name like `trades.w3.log´.
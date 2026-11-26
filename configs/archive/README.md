# Retired configurations

Retired inputs are retained here rather than mixed into the active yard,
schedule, initial-condition, and experiment libraries. Automatic comparison
scripts discover only active `configs/experiments/traffic/` and `queues/` presets.

- [legacy_single_bay_14h/experiment.yaml](legacy_single_bay_14h/experiment.yaml)
  preserves the original `example` demonstration: one bay, one loader, one pile,
  one mill, three trucks, and an 840-minute horizon. The shorter active traffic
  cases replace it for onboarding and general comparisons. It remains useful for
  historical output and single-bay regression checks.

Each archived scenario keeps its component files together and remains loadable
through the normal preset interface. Inputs were renamed/moved without changing
their physical definitions. Adding or removing an archived scenario from a batch
must be an explicit experimental choice.

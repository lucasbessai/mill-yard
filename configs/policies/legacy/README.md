# Legacy feature-policy settings

These profiles reproduce earlier feature definitions. They are excluded from
normal profile comparisons unless explicitly requested:

| Profile | Definition |
| --- | --- |
| [feature_scored__v2_4weights.yaml](feature_scored__v2_4weights.yaml) | Clipped individual wait and global waiting queue; bay release disabled |
| [feature_scored__v3_4weights.yaml](feature_scored__v3_4weights.yaml) | Smooth individual wait and local backlog; bay release disabled |
| [feature_scored__v3_5weights.yaml](feature_scored__v3_5weights.yaml) | Same version-3 definitions with a nonzero bay-release weight |

Current version-4 profiles live one directory above and include area waiting
urgency inside the truck feature. Legacy profiles remain functional for
reproducibility; moving them here does not remove support for their versions.

# tag_qc - Revit tag QC (Dynamo Python)

Dynamo CPython3 node for Revit 2022+. Works on sleeves and chippings that live in **linked** models, checked and annotated in your documentation model.

## What it does (per view)
- Fixes sleeve tags whose type (Circ / Rect) doesn't match the sleeve shape
- Tags every untagged sleeve and chipping with the right tag type
- Places new tags so they don't overlap other tags / dimension text and leaders don't cross
- Marks everything it adds or changes in neon green (view override)
- Reports remaining clashes; writes a CSV report

## Use
1. Put the code in a Python Script node, add inputs `IN[0]` (Run) and `IN[1]` (Apply) as Booleans.
2. Edit the config block at the top (`VIEW_NAMES`, tag family/type names, sleeve/chipping family keys).
3. Run with Apply = False for a report only, then Apply = True.

`tag_cleanup.py` removes wrong-category tags (chipping tags on sleeves and vice versa).

## Known limitations
- Tag overlap uses Revit's tag bounding box, which may include the leader.
- Existing tags and dimensions are never moved.

Always test on a copy of the model first.

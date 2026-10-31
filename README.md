# revit-sleeves-qc

Dynamo (CPython3) scripts for builder's work sleeves in Revit 2022+.
Sleeves are placed in the drainage model; pipes, ducts and cable trays come from linked models.

| Folder | Script | What it does | Changes the model? |
|---|---|---|---|
| `sleeve_tagging/` | `tag_qc.py`, `tag_cleanup.py` | QC and tagging of sleeves and chippings in linked models | Yes (adds/fixes tags) |
| `sleeve_checks/` | `sleeve_check.py` | Reports sleeves with a wrong `Opening System` or a wrongly sized opening | **No** (writes a CSV only) |
| `sleeve_checks/` | `sleeve_fix.py` | Writes the correct `Opening System` on sleeves where it is wrong | **Yes** |

## sleeve_check.py / sleeve_fix.py

For every `Wall Circ Opening` / `Wall Rect Opening` on the chosen level (`Level_Code = GF`):

1. **Finds what passes through the sleeve.** It tests whether the centreline of each pipe, duct and cable tray (host model and links) crosses the sleeve's bounding box.
2. **Opening System.** The expected value comes from where the element lives:
   link name contains `FP` -> `FP`, `WS` -> `WS`, `ME` -> `HVAC`, `IR` -> `IR`, `EL` -> `ELEC`; the active model (not a link) -> `DR`.
   Several systems in one sleeve are joined, e.g. `FP + HVAC + ELEC`.
3. **Size** (`sleeve_check.py` only). Opening Diameter / Opening Width x Height against the pipe or duct size (larger of Outside Diameter and Overall Size, plus insulation) with a clearance of 25 mm per side. Reports `TOO SMALL` and `TOO BIG`.

`sleeve_check.py` writes `sleeve_check.csv` (only the sleeves that need attention) to your Desktop. `sleeve_fix.py` writes a log of every change to `sleeve_fix_log.csv`.

## Use

1. Add a **Python Script** node (CPython3), paste the script, click **Save Changes**.
2. Connect a **Watch** node to the output and run in **Manual** mode.
3. Optional: wire a file path (`.csv`) into `IN[0]` to choose where the file is saved.
4. Edit the config block at the top (family names, level parameter, link tokens, clearances).

> **`sleeve_fix.py` writes to the model.** Save the file first. One Ctrl+Z in Revit undoes the whole run.

## Notes and limits

- The geometry test is a bounding-box test, not a true clash. Rotated sleeves can catch a pipe passing close by.
- Fittings are not included, only straight pipe, duct and cable tray segments.
- Sleeves with more than one element passing through are not size-checked.
- Link names must contain the system token (`FP`, `WS`, `ME`, `IR`, `EL`) as a separate word, e.g. `PRJ_ME_B1`.

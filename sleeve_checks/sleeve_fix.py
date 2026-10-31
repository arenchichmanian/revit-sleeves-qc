# -*- coding: utf-8 -*-
# Dynamo Python node: FIXES "Opening System" on Wall Circ / Rect Opening sleeves (GF only).
# It WRITES TO THE MODEL. Changes a sleeve when its "Opening System" is wrong.
# Expected value comes from where the pipes/ducts/trays through it live:
#   link name token FP -> "FP", WS -> "WS", ME -> "HVAC", IR -> "IR", EL -> "ELEC",
#   active model (not a link) -> "DR"
# Several systems in one sleeve are joined, e.g. "FP + HVAC + ELEC".
# Sleeves with no pipe/duct/tray found are left alone.
# Only the instance parameter is written (never the family type).
# Writes a log CSV of every change. Optional IN[0] = full path (.csv). Default: sleeve_fix_log.csv on Desktop.
# OUT = [log_path, number_of_sleeves_changed, total_sleeves_checked]

import clr, re, math, os, io
clr.AddReference('RevitAPI')
clr.AddReference('RevitServices')
from Autodesk.Revit.DB import *
from Autodesk.Revit.DB.Plumbing import Pipe
from Autodesk.Revit.DB.Mechanical import Duct
from Autodesk.Revit.DB.Electrical import CableTray
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager

try:
    unicode
except NameError:
    unicode = str

doc = DocumentManager.Instance.CurrentDBDocument

# ------------------------- CONFIG -------------------------
FIX_SYSTEM = True               # True = WRITE the correct "Opening System" where it is wrong.
                                # False = report only (changes nothing)
FT = 304.8                      # feet -> mm
CLEARANCE_MM = 50.0             # target gap on SIZE (total) = 25 mm each side. Smaller opening = TOO SMALL
OVERSIZE_TOL_MM = 25.0          # opening may exceed target by this much (total) before it is TOO BIG
INCLUDE_INSULATION = True       # add 2 x insulation thickness to member size
SEARCH_TOL_MM = 50.0            # box expansion when looking for members inside the sleeve

CIRC_FAMILY = "Wall Circ Opening"
RECT_FAMILY = "Wall Rect Opening"
P_SYSTEM, P_DIA, P_W, P_H = "Opening System", "Opening Diameter", "Opening Width", "Opening Height"

# token in link name -> value expected in "Opening System"
LINK_VALUES = {"FP": "FP", "WS": "WS", "ME": "HVAC", "IR": "IR", "EL": "ELEC"}
HOST_VALUE = "DR"               # active model = drainage
LEVEL_PARAM = "Level_Code"       # sleeve parameter that holds the floor
LEVEL_NAME = "GF"               # only check sleeves where Level_Code = this (ground floor). None = all
# -----------------------------------------------------------


def get_param(el, name):
    p = el.LookupParameter(name)
    if p is None and hasattr(el, "Symbol") and el.Symbol is not None:
        p = el.Symbol.LookupParameter(name)
    return p

def pval_len(el, name):
    p = get_param(el, name)
    if p is None or not p.HasValue:
        return None
    return p.AsDouble() * FT

def pval_str(el, name):
    p = get_param(el, name)
    if p is None:
        return None
    s = p.AsString()
    if s is None:
        s = p.AsValueString()
    return (s or "").strip()

def bip_len(el, bip):
    p = el.get_Parameter(bip)
    return p.AsDouble() * FT if (p is not None and p.HasValue) else 0.0

def link_value(name):
    toks = set(t for t in re.split(r'[^A-Z0-9]+', name.upper()) if t)
    for k in LINK_VALUES:
        if k in toks:
            return LINK_VALUES[k]
    return None

def overall_size_mm(el):
    """'Overall Size' parameter in mm (0 if missing). Handles numeric or text values like '110 mm'."""
    p = el.LookupParameter("Overall Size")
    if p is None or not p.HasValue:
        return 0.0
    if p.StorageType == StorageType.Double:
        return p.AsDouble() * FT
    s = p.AsString() or p.AsValueString() or ""
    nums = [float(x) for x in re.findall(r'\d+\.?\d*', s)]
    if not nums:
        return 0.0
    v = max(nums)
    low = s.lower()
    if "mm" not in low and ('"' in s or " in" in low):
        v *= 25.4
    return v

def member_size(el):
    """returns (kind, d, w, h) in mm incl. insulation if enabled"""
    ins = 2 * bip_len(el, BuiltInParameter.RBS_REFERENCE_INSULATION_THICKNESS) if INCLUDE_INSULATION else 0.0
    if isinstance(el, Pipe):
        od = bip_len(el, BuiltInParameter.RBS_PIPE_OUTER_DIAMETER) + ins
        return ("pipe", max(od, overall_size_mm(el)), None, None)
    if isinstance(el, CableTray):
        return ("tray", None, bip_len(el, BuiltInParameter.RBS_CABLETRAY_WIDTH_PARAM),
                bip_len(el, BuiltInParameter.RBS_CABLETRAY_HEIGHT_PARAM))
    d = bip_len(el, BuiltInParameter.RBS_CURVE_DIAMETER_PARAM)
    if d > 0:
        return ("rdct", d + ins, None, None)
    return ("duct", None, bip_len(el, BuiltInParameter.RBS_CURVE_WIDTH_PARAM) + ins,
            bip_len(el, BuiltInParameter.RBS_CURVE_HEIGHT_PARAM) + ins)

def seg_hits_box(p0, p1, bmin, bmax):
    t0, t1 = 0.0, 1.0
    for a, b, lo, hi in ((p0.X, p1.X, bmin.X, bmax.X), (p0.Y, p1.Y, bmin.Y, bmax.Y), (p0.Z, p1.Z, bmin.Z, bmax.Z)):
        d = b - a
        if abs(d) < 1e-9:
            if a < lo or a > hi:
                return False
        else:
            ta, tb = (lo - a) / d, (hi - a) / d
            if ta > tb:
                ta, tb = tb, ta
            t0, t1 = max(t0, ta), min(t1, tb)
            if t0 > t1:
                return False
    return True

def on_level(fi):
    """sleeve is on the wanted floor if its Level_Code parameter equals LEVEL_NAME (e.g. GF)"""
    if not LEVEL_NAME:
        return True
    v = pval_str(fi, LEVEL_PARAM)
    return v is not None and v.strip().upper() == LEVEL_NAME.strip().upper()

SYS_ORDER = ["DR", "WS", "FP", "HVAC", "IR", "ELEC"]     # order used when joining several systems

def sys_rank(v):
    return SYS_ORDER.index(v) if v in SYS_ORDER else len(SYS_ORDER)

def sys_set(s):
    """'FP + HVAC', 'hvac,fp', 'FP/HVAC' ... -> {'FP','HVAC'} (so order/separator don't matter)"""
    return set(t for t in re.split(r'[^A-Z0-9]+', (s or "").upper()) if t)

def eid(e):
    return e.Id.IntegerValue if hasattr(e.Id, "IntegerValue") else e.Id.Value

def csv_cell(v):
    s = unicode(v)
    if any(c in s for c in ',"\n'):
        s = '"' + s.replace('"', '""') + '"'
    return s

# ---------- gather members (active model = DR, links = FP/WS/ME...) ----------
members = []   # dict: el, p0, p1, val, src
def add_members(d, xf, val, src):
    for cls in (Pipe, Duct, CableTray):
        for el in FilteredElementCollector(d).OfClass(cls).WhereElementIsNotElementType():
            loc = el.Location
            if not isinstance(loc, LocationCurve):
                continue
            c = loc.Curve
            p0, p1 = c.GetEndPoint(0), c.GetEndPoint(1)
            if xf is not None:
                p0, p1 = xf.OfPoint(p0), xf.OfPoint(p1)
            members.append({"el": el, "p0": p0, "p1": p1, "val": val, "src": src})

add_members(doc, None, HOST_VALUE, "active model")
skipped_links = []
for li in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
    ld = li.GetLinkDocument()
    if ld is None:
        continue
    v = link_value(li.Name) or link_value(ld.Title)
    if v is None:
        skipped_links.append(li.Name)
        continue
    add_members(ld, li.GetTotalTransform(), v, li.Name)

# ---------- sleeves ----------
sleeves = []
for fi in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_GenericModel).OfClass(FamilyInstance):
    fn = fi.Symbol.Family.Name
    if not (fn.startswith(CIRC_FAMILY) or fn.startswith(RECT_FAMILY)):
        continue
    if not on_level(fi):
        continue
    if fn.startswith(CIRC_FAMILY):
        sleeves.append((fi, "circ"))
    elif fn.startswith(RECT_FAMILY):
        sleeves.append((fi, "rect"))

tol = SEARCH_TOL_MM / FT
rows = []
pending = []

for fi, kind in sleeves:
    bb = fi.get_BoundingBox(None)
    if bb is None:
        continue
    bmin = XYZ(bb.Min.X - tol, bb.Min.Y - tol, bb.Min.Z - tol)
    bmax = XYZ(bb.Max.X + tol, bb.Max.Y + tol, bb.Max.Z + tol)

    hits = [m for m in members if seg_hits_box(m["p0"], m["p1"], bmin, bmax)]
    pv = pval_str(fi, P_SYSTEM)
    issues = []
    should_be = ""
    fix = None
    vals = sorted(set(m["val"] for m in hits))
    srcs = sorted(set(m["src"] for m in hits))

    # --- 1) Opening System
    if not hits:
        issues.append("NO PIPE/DUCT FOUND")
    else:
        should_be = " + ".join(sorted(vals, key=sys_rank))     # e.g. "FP + HVAC + ELEC"
        if get_param(fi, P_SYSTEM) is None:
            issues.append("PARAM '%s' MISSING" % P_SYSTEM)
        elif sys_set(pv) != set(vals):
            if FIX_SYSTEM:
                fix = should_be
                issues.append("WRONG SYSTEM - FIXED (was '%s')" % (pv or ""))
            else:
                issues.append("WRONG SYSTEM")

    # --- 2) size check
    open_txt, need_txt = "", ""
    if hits:
        if len(hits) > 1:
            issues.append("MULTIPLE MEMBERS (%d) - SIZE NOT CHECKED" % len(hits))
        else:
            kind_m, d, w, h = member_size(hits[0]["el"])
            if kind == "circ":
                od = pval_len(fi, P_DIA)
                open_txt = "%.0f" % od if od else "?"
                need = (d if d is not None else math.sqrt(w * w + h * h)) + CLEARANCE_MM
                need_txt = "%.0f" % need
                if od is None:
                    issues.append("PARAM '%s' MISSING" % P_DIA)
                elif od + 0.5 < need:
                    issues.append("TOO SMALL (short by %.0f)" % (need - od))
                elif od - need > OVERSIZE_TOL_MM + 0.5:
                    issues.append("TOO BIG (+%.0f over target)" % (od - need))
            else:
                ow, oh = pval_len(fi, P_W), pval_len(fi, P_H)
                open_txt = "%s x %s" % ("%.0f" % ow if ow else "?", "%.0f" % oh if oh else "?")
                if d is not None:
                    rw = rh = d + CLEARANCE_MM
                else:
                    rw, rh = w + CLEARANCE_MM, h + CLEARANCE_MM
                need_txt = "%.0f x %.0f" % (rw, rh)
                if ow is None or oh is None:
                    issues.append("PARAM '%s/%s' MISSING" % (P_W, P_H))
                elif ow + 0.5 < rw or oh + 0.5 < rh:
                    issues.append("TOO SMALL (W short %.0f, H short %.0f)" % (max(0, rw - ow), max(0, rh - oh)))
                elif ow - rw > OVERSIZE_TOL_MM + 0.5 or oh - rh > OVERSIZE_TOL_MM + 0.5:
                    issues.append("TOO BIG (W +%.0f, H +%.0f over target)" % (ow - rw, oh - rh))

    if issues:
        rows.append([eid(fi), fi.Symbol.Family.Name, pv or "", should_be, "/".join(srcs),
                     open_txt, need_txt, "; ".join(issues)])
        if fix is not None:
            pending.append((fi, fix, len(rows) - 1))

# ---------- apply the fixes (one transaction) ----------
fixed_n = 0
if pending:
    TransactionManager.Instance.EnsureInTransaction(doc)
    for fi, newval, ri in pending:
        ok = False
        p = fi.LookupParameter(P_SYSTEM)   # instance parameter only - never touches the family type
        if p is not None and (not p.IsReadOnly) and p.StorageType == StorageType.String:
            try:
                ok = bool(p.Set(newval))
            except Exception:
                ok = False
        if ok:
            fixed_n += 1
        else:
            rows[ri][7] = rows[ri][7].replace("FIXED", "NOT WRITTEN (parameter is read-only or a type parameter)")
    TransactionManager.Instance.TransactionTaskDone()

# keep only the sleeves we touched (or tried to) in the log
rows = [r for r in rows if "FIXED" in r[7] or "NOT WRITTEN" in r[7]]

# ---------- write log CSV ----------
try:
    out_path = IN[0]
except Exception:
    out_path = None
if not out_path:
    home = os.path.expanduser("~")
    cands = []
    for ev in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        if os.environ.get(ev):
            cands.append(os.path.join(os.environ[ev], "Desktop"))
    cands += [os.path.join(home, "OneDrive", "Desktop"), os.path.join(home, "Desktop"),
              os.path.join(home, "Documents"), home]
    folder = home
    for c in cands:
        if os.path.isdir(c):
            folder = c
            break
    out_path = os.path.join(folder, "sleeve_fix_log.csv")

header = ["ElementId", "Family", "Opening System (current)", "Opening System (should be)",
          "Found in", "Opening size (mm)", "Required size (mm)", "Problem"]
rows.sort(key=lambda r: (r[7], r[0]))
lines = [",".join(csv_cell(c) for c in header)]
for r in rows:
    lines.append(",".join(csv_cell(c) for c in r))
if skipped_links:
    lines.append(",".join(csv_cell(c) for c in
                 ["-", "WARNING: links skipped (no token from LINK_VALUES in name)", "", "", ", ".join(skipped_links), "", "", ""]))

with io.open(out_path, "w", encoding="utf-8-sig", newline="") as f:
    f.write(unicode("\r\n".join(lines) + "\r\n"))

OUT = [out_path, fixed_n, len(sleeves)]

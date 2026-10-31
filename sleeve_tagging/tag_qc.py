# -*- coding: utf-8 -*-
# =====================================================================
# TAG QC + AUTO-TAG  -  Dynamo Python node (CPython3 / IronPython2), Revit 2022+
#
# Per view it:
#   1. Fixes sleeve tags whose type (Circ/Rect) doesn't match the sleeve shape   -> neon green
#   2. Tags every untagged sleeve (Circ/Rect tag by shape) and chipping          -> neon green
#   3. New / changed tags are placed so they never overlap other tags or dimension
#      text and their leaders never cross other leaders (tries 40 positions each)
#   4. Reports whatever is still clashing (pre-existing stuff is NOT moved)
#
# Dynamo inputs:  IN[0] = Run (bool)     IN[1] = Apply changes (bool)  (False = report only)
# Output: list of report lines (also saved to CSV on your Desktop)
# =====================================================================
import clr, math, os
clr.AddReference('RevitAPI')
clr.AddReference('RevitServices')
from Autodesk.Revit.DB import *
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager

doc = DocumentManager.Instance.CurrentDBDocument

# ------------------------- CONFIG (edit me) --------------------------
VIEW_NAMES = [
    "GROUND FLOOR - BUILDERS WORK - PART E",
    "GROUND FLOOR - BUILDERS WORK - PART D",
    "GROUND FLOOR - BUILDERS WORK - PART C",
]

SLEEVE_TAG_FAMILY = "Opening-Tag(1mm)"     # family used for NEW sleeve tags (types: Circ / Rect)
SLEEVE_TAG_FAMILIES = ["Opening-Tag(1mm)", "Opening-Tag"]   # all families that count as a sleeve tag
CHIP_TAG_FAMILY   = "Chipping-Tag(1mm)"
CHIP_TAG_TYPE     = "Circ"

SLEEVE_FAMILY_KEYS = ["wall rect opening", "wall circ opening",
                      "wallhostedrectangular"]   # last = GM-WallHostedRectangular (remove if not tagged)
CHIP_FAMILY_KEYS   = ["chipping"]

ROUND_PARAMS = ["Opening Diameter"]
RECT_PARAMS  = ["Opening Width", "Opening Length", "Width"]
ROUND_WORDS  = ["circ", "round"]
RECT_WORDS   = ["rect", "square"]

# what to do (set to True/False).
DO_TYPE_FIX     = True   # fix Circ/Rect on sleeve tags
DO_TAGS         = True   # tag untagged sleeves / chippings
DO_CLASH_REPORT = False   # list remaining tag clashes at the end

NEON_GREEN = Color(57, 255, 20)
TAG_STEP_MM_PAPER   = 10.0                       # candidate spacing for tag heads (paper mm)
TAG_STEPS           = (1.0, 2.0, 3.5, 5.0, 7.0)  # multiples of the step
DESKTOP = r"D:\OneDrive - Dulb Trading and Contracting Co Ltd\Desktop"
# ---------------------------------------------------------------------

RUN   = IN[0] if len(IN) > 0 else False
APPLY = IN[1] if len(IN) > 1 else False
MM = 1.0 / 304.8


def iid(i):
    try:
        return int(i.Value)
    except Exception:
        return i.IntegerValue

def uv(view, p):
    return (p.DotProduct(view.RightDirection), p.DotProduct(view.UpDirection))

def rect_ov(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]

def seg_x(p1, p2, p3, p4):
    def o(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
    return (o(p3, p4, p1)*o(p3, p4, p2) < 0) and (o(p1, p2, p3)*o(p1, p2, p4) < 0)

def in_box(p, b):
    return b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]

def seg_hits_box(s, b):
    if in_box(s[0], b) or in_box(s[1], b):
        return True
    c = [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]
    for i in range(4):
        if seg_x(s[0], s[1], c[i], c[(i+1) % 4]):
            return True
    return False

def fam_name(e):
    try:
        return e.Symbol.FamilyName
    except Exception:
        return ""

def type_name(sym):
    try:
        return sym.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM).AsString()
    except Exception:
        return sym.Name

def new_ogs():
    o = OverrideGraphicSettings()
    o.SetProjectionLineColor(NEON_GREEN)
    return o

def mid(mn, mx):
    return XYZ((mn.X+mx.X)/2, (mn.Y+mx.Y)/2, (mn.Z+mx.Z)/2)

# ---------------------- linked element handling ----------------------
def host_bbox(e, tf):
    bb = e.get_BoundingBox(None)
    if bb is None:
        return None
    pts = [tf.OfPoint(XYZ(x, y, z)) for x in (bb.Min.X, bb.Max.X)
           for y in (bb.Min.Y, bb.Max.Y) for z in (bb.Min.Z, bb.Max.Z)]
    mn = XYZ(min(p.X for p in pts), min(p.Y for p in pts), min(p.Z for p in pts))
    mx = XYZ(max(p.X for p in pts), max(p.Y for p in pts), max(p.Z for p in pts))
    return mn, mx

def in_view(view, mn, mx):
    c = mid(mn, mx)
    if view.CropBoxActive:
        cb = view.CropBox
        p = cb.Transform.Inverse.OfPoint(c)
        if not (cb.Min.X <= p.X <= cb.Max.X and cb.Min.Y <= p.Y <= cb.Max.Y):
            return False
    if isinstance(view, ViewPlan):
        try:
            vr = view.GetViewRange()
            def pz(pl):
                l = doc.GetElement(vr.GetLevelId(pl))
                return l.Elevation + vr.GetOffset(pl)
            if mx.Z < pz(PlanViewPlane.ViewDepth) or mn.Z > pz(PlanViewPlane.TopClipPlane):
                return False
        except Exception:
            pass
    return True

def has_param(e, names):
    t = e.Document.GetElement(e.GetTypeId())
    for n in names:
        if e.LookupParameter(n) is not None or (t is not None and t.LookupParameter(n) is not None):
            return True
    return False

def sleeve_shape(e):
    nm = (fam_name(e) + " " + e.Name).lower()
    if any(w in nm for w in ROUND_WORDS):
        return "Circ"
    if any(w in nm for w in RECT_WORDS):
        return "Rect"
    if has_param(e, ROUND_PARAMS):
        return "Circ"
    if has_param(e, RECT_PARAMS):
        return "Rect"
    return None

def collect_linked(view):
    sleeves, chips = {}, {}
    for link in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
        ldoc = link.GetLinkDocument()
        if ldoc is None:
            continue
        tf = link.GetTotalTransform()
        for e in FilteredElementCollector(ldoc).OfClass(FamilyInstance):
            fn = fam_name(e).lower()
            is_s = any(k in fn for k in SLEEVE_FAMILY_KEYS)
            is_c = any(k in fn for k in CHIP_FAMILY_KEYS)
            if not (is_s or is_c):
                continue
            hb = host_bbox(e, tf)
            if hb is None or not in_view(view, hb[0], hb[1]):
                continue
            c = mid(hb[0], hb[1])
            org = c
            try:
                if isinstance(e.Location, LocationPoint):
                    org = tf.OfPoint(e.Location.Point)
            except Exception:
                pass
            d = {"key": (iid(link.Id), iid(e.Id)), "e": e, "link": link, "tf": tf, "pt": c, "org": org,
                 "shape": sleeve_shape(e) if is_s else None,
                 "label": "{0} | {1} | linkedId {2}".format(link.Name, fam_name(e), iid(e.Id))}
            (sleeves if is_s else chips)[d["key"]] = d
    return sleeves, chips


def ref_key(r):
    return (iid(r.ElementId), iid(r.LinkedElementId))

def ref_point(r):
    link = doc.GetElement(r.ElementId)
    e = link.GetLinkDocument().GetElement(r.LinkedElementId)
    hb = host_bbox(e, link.GetTotalTransform())
    return mid(hb[0], hb[1])

# ---------------------- tag / dimension geometry ----------------------
def tag_targets(tag):
    try:
        return frozenset(ref_key(r) for r in tag.GetTaggedReferences())
    except Exception:
        return frozenset()

def tag_box(tag, view):
    bb = tag.get_BoundingBox(view)
    if bb is None:
        return None
    pts = [uv(view, XYZ(x, y, z)) for x in (bb.Min.X, bb.Max.X)
           for y in (bb.Min.Y, bb.Max.Y) for z in (bb.Min.Z, bb.Max.Z)]
    return (min(p[0] for p in pts), min(p[1] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts))

def tag_segments(tag, view):
    segs = []
    try:
        if not tag.HasLeader:
            return segs
        head = tag.TagHeadPosition
        for r in tag.GetTaggedReferences():
            end = None
            try:
                if tag.LeaderEndCondition == LeaderEndCondition.Free:
                    end = tag.GetLeaderEnd(r)
            except Exception:
                pass
            if end is None:
                end = ref_point(r)
            pts = [head]
            try:
                if tag.HasElbow(r):
                    pts.append(tag.GetLeaderElbow(r))
            except Exception:
                pass
            pts.append(end)
            u = [uv(view, p) for p in pts]
            for i in range(len(u)-1):
                segs.append((u[i], u[i+1]))
    except Exception:
        pass
    return segs

def tag_info(tag, view):
    return {"id": iid(tag.Id), "tag": tag, "tg": tag_targets(tag),
            "box": tag_box(tag, view), "segs": tag_segments(tag, view)}

def tag_label(tag):
    s = doc.GetElement(tag.GetTypeId())
    return "Tag {0} ({1}:{2})".format(iid(tag.Id), s.FamilyName, type_name(s))

def dim_boxes(view, dims):
    out = []
    for d in dims:
        try:
            th = d.DimensionType.get_Parameter(BuiltInParameter.TEXT_SIZE).AsDouble() * view.Scale
            parts = list(d.Segments) if d.NumberOfSegments > 0 else [d]
            for s in parts:
                txt = (s.Prefix or "") + (s.ValueString or "") + (s.Suffix or "")
                w = max(len(txt), 2) * th * 0.8
                c = uv(view, s.TextPosition)
                out.append(((c[0]-w/2, c[1]-th/2, c[0]+w/2, c[1]+th/2), iid(d.Id)))
        except Exception:
            continue
    return out


def clashes(info, others, dboxes):
    res = []
    for o in others:
        if o["id"] == info["id"]:
            continue
        if info["box"] and o["box"] and rect_ov(info["box"], o["box"]):
            res.append(("tag overlap", o["id"]))
        if o["tg"] != info["tg"]:
            hit = False
            for s1 in info["segs"]:
                for s2 in o["segs"]:
                    if seg_x(s1[0], s1[1], s2[0], s2[1]):
                        hit = True
                        break
                if hit:
                    break
            if hit:
                res.append(("leader crosses", o["id"]))
            elif o["box"] and any(seg_hits_box(s, o["box"]) for s in info["segs"]):
                res.append(("leader through tag", o["id"]))
            elif info["box"] and any(seg_hits_box(s, info["box"]) for s in o["segs"]):
                res.append(("leader through tag", o["id"]))
    if info["box"]:
        for db, did in dboxes:
            if rect_ov(info["box"], db):
                res.append(("overlaps dim text", did))
    return res

def cands(pt, view):
    base = TAG_STEP_MM_PAPER * MM * view.Scale
    R, U = view.RightDirection, view.UpDirection
    out = []
    for k in TAG_STEPS:
        for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)):
            out.append(pt.Add(R.Multiply(dx*k*base)).Add(U.Multiply(dy*k*base)))
    return out

def place_free(tag, view, anchor, others, dboxes):
    """move tag head over candidates until nothing clashes. returns (info, ok)"""
    best, bestn, bestpt = None, 10**6, None
    for pt in cands(anchor, view):
        tag.TagHeadPosition = pt
        doc.Regenerate()
        info = tag_info(tag, view)
        c = clashes(info, others, dboxes)
        if not c:
            return info, True
        if len(c) < bestn:
            best, bestn, bestpt = info, len(c), pt
    tag.TagHeadPosition = bestpt
    doc.Regenerate()
    return tag_info(tag, view), False

# ------------------------------ tag symbols ---------------------------
def find_syms():
    d = {}
    for s in FilteredElementCollector(doc).OfClass(FamilySymbol):
        if s.FamilyName in SLEEVE_TAG_FAMILIES + [CHIP_TAG_FAMILY]:
            d[(s.FamilyName, type_name(s))] = s
    return d

def set_type(el, type_id):
    try:
        p = el.get_Parameter(BuiltInParameter.ELEM_TYPE_PARAM)
        if p is not None and not p.IsReadOnly and p.Set(type_id):
            return
    except Exception:
        pass
    el.ChangeTypeId(type_id)

def make_tag(sym, view, el, pt):
    if not sym.IsActive:
        sym.Activate()
        doc.Regenerate()
    ref = Reference(el["e"]).CreateLinkReference(el["link"])
    try:
        return IndependentTag.Create(doc, sym.Id, view.Id, ref, True, TagOrientation.Horizontal, pt)
    except Exception:
        t = IndependentTag.Create(doc, view.Id, ref, True, TagMode.TM_ADDBY_CATEGORY,
                                  TagOrientation.Horizontal, pt)
        set_type(t, sym.Id)
        return t











# ------------------------------- main --------------------------------
report = []
if not RUN:
    OUT = ["Set IN[0]=True to run. IN[1]=True applies changes (otherwise report only)."]
else:
    views = []
    names = {}
    for v in FilteredElementCollector(doc).OfClass(View):
        if (not v.IsTemplate) and v.Name in VIEW_NAMES:
            views.append(v)
            names[v.Name] = 1
    for vn in VIEW_NAMES:
        if vn not in names:
            report.append([vn, "ERROR", "view not found"])

    if APPLY:
        TransactionManager.Instance.EnsureInTransaction(doc)
    syms = find_syms()
    ogs = new_ogs()
    sleeve_sym = {"Circ": syms.get((SLEEVE_TAG_FAMILY, "Circ")), "Rect": syms.get((SLEEVE_TAG_FAMILY, "Rect"))}
    chip_sym = syms.get((CHIP_TAG_FAMILY, CHIP_TAG_TYPE))
    for k, s in (("sleeve Circ", sleeve_sym["Circ"]), ("sleeve Rect", sleeve_sym["Rect"]), ("chip", chip_sym)):
        if s is None:
            report.append(["-", "ERROR", "tag type not found: " + k])

    for view in views:
        V = view.Name
        sleeves, chips = collect_linked(view)
        report.append([V, "INFO", "{0} sleeves, {1} chippings in view".format(len(sleeves), len(chips))])
        tags = [t for t in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag)]
        dims = [d for d in FilteredElementCollector(doc, view.Id).OfClass(Dimension)]
        others = [tag_info(t, view) for t in tags]
        dboxes = dim_boxes(view, dims)

        # ---- 1. fix wrong sleeve tag types ----
        for info in (list(others) if DO_TYPE_FIX else []):
            t = info["tag"]
            s = doc.GetElement(t.GetTypeId())
            if s.FamilyName not in SLEEVE_TAG_FAMILIES:
                continue
            for k in info["tg"]:
                sl = sleeves.get(k)
                if sl is None or sl["shape"] is None:
                    if sl is not None:
                        report.append([V, "SHAPE UNKNOWN", sl["label"]])
                    continue
                if type_name(s) != sl["shape"]:
                    own = syms.get((s.FamilyName, sl["shape"]))
                    if APPLY and own is not None:
                        set_type(t, own.Id)
                        doc.Regenerate()
                        view.SetElementOverrides(t.Id, ogs)
                        ni = tag_info(t, view)
                        rest = [o for o in others if o["id"] != info["id"]]
                        c = clashes(ni, rest, dboxes)
                        if c:
                            ni, ok = place_free(t, view, sl["pt"], rest, dboxes)
                            if not ok:
                                report.append([V, "CLASH (changed tag)", tag_label(t)])
                        others[others.index(info)] = ni
                        report.append([V, "TYPE CHANGED", "{0}: {1} -> {2} (neon)".format(sl["label"], type_name(s), sl["shape"])])
                    else:
                        report.append([V, "WRONG TAG TYPE", "{0} is {1}, tag is {2}".format(sl["label"], sl["shape"], type_name(s))])

        # ---- 2. tag untagged sleeves + chippings ----
        tagged_all = set()
        tagged_s = set()
        for info in others:
            tagged_all |= info["tg"]
            if doc.GetElement(info["tag"].GetTypeId()).FamilyName in SLEEVE_TAG_FAMILIES:
                tagged_s |= info["tg"]
        todo = []
        for k, sl in (sleeves.items() if DO_TAGS else []):
            if k not in tagged_s:
                sym = sleeve_sym.get(sl["shape"]) if sl["shape"] else None
                if sl["shape"] is None:
                    report.append([V, "SHAPE UNKNOWN", sl["label"]])
                else:
                    todo.append((sl, sym, "SLEEVE"))
        for k, ch in (chips.items() if DO_TAGS else []):
            if k not in tagged_all:
                todo.append((ch, chip_sym, "CHIPPING"))
        for el, sym, kind in todo:
            if not APPLY:
                report.append([V, "UNTAGGED " + kind, el["label"]])
                continue
            if sym is None:
                report.append([V, "ERROR", "no tag type for " + el["label"]])
                continue
            try:
                t = make_tag(sym, view, el, cands(el["pt"], view)[0])
                doc.Regenerate()
                info, ok = place_free(t, view, el["pt"], others, dboxes)
                view.SetElementOverrides(t.Id, ogs)
                others.append(info)
                report.append([V, "TAG ADDED", "{0} {1} (neon){2}".format(
                    kind, el["label"], "" if ok else "  ** could not find clash-free spot **")])
            except Exception as ex:
                report.append([V, "ERROR", "tagging {0}: {1}".format(el["label"], ex)])

        # ---- 3. remaining clashes ----
        seen = set()
        for info in (others if DO_CLASH_REPORT else []):
            for kind, oid in clashes(info, others, dboxes):
                key = (kind, min(info["id"], oid), max(info["id"], oid))
                if key in seen:
                    continue
                seen.add(key)
                report.append([V, "CLASH", "{0}: tag {1} / {2}".format(kind, info["id"], oid)])

    if APPLY:
        TransactionManager.Instance.TransactionTaskDone()

    csv_msg = ""
    try:
        p = None
        for dd in (DESKTOP, os.path.expanduser("~"), os.environ.get("TEMP", ".")):
            try:
                tp = os.path.join(dd, "tag_qc_report.csv")
                open(tp, "w").close()
                p = tp
                break
            except Exception:
                continue
        with open(p, "w") as f:
            f.write("View,Issue,Detail\n")
            for r in report:
                f.write('"{0}","{1}","{2}"\n'.format(r[0], r[1], str(r[2]).replace('"', "'")))
        csv_msg = "CSV saved to: " + p
    except Exception as ex:
        csv_msg = "CSV NOT saved: " + str(ex)
    OUT = [csv_msg] + [" | ".join([str(x) for x in r]) for r in report]

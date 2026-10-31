# -*- coding: utf-8 -*-
# =====================================================================
# TAG CLEANUP - Dynamo Python node (CPython3 / IronPython2), Revit 2022+
# Deletes wrong-category tags in the listed views:
#   - chipping tag (chipping tag) placed on a SLEEVE
#   - sleeve tag (sleeve tag) placed on a CHIPPING
# Run tag_qc.py afterwards (Apply=True) to re-tag those elements correctly.
#
# Dynamo inputs: IN[0] = Run (bool)   IN[1] = Delete (bool)  (False = just list them)
# =====================================================================
import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitServices')
from Autodesk.Revit.DB import *
from RevitServices.Persistence import DocumentManager
from RevitServices.Transactions import TransactionManager

doc = DocumentManager.Instance.CurrentDBDocument

VIEW_NAMES = [
    "YOUR VIEW NAME 1",
    "YOUR VIEW NAME 2",
    "YOUR VIEW NAME 3",
]
SLEEVE_TAG_FAMILY = "Sleeve-Tag-A"
CHIP_TAG_FAMILY   = "Chipping-Tag"
SLEEVE_FAMILY_KEYS = ["rect opening", "circ opening", "wallhostedrectangular"]
CHIP_FAMILY_KEYS   = ["chipping for camera"]

RUN    = IN[0] if len(IN) > 0 else False
DELETE = IN[1] if len(IN) > 1 else False


def iid(i):
    try:
        return int(i.Value)
    except Exception:
        return i.IntegerValue

def kind_of(ref):
    """'sleeve' / 'chip' / None for a tagged (linked) reference"""
    try:
        link = doc.GetElement(ref.ElementId)
        e = link.GetLinkDocument().GetElement(ref.LinkedElementId)
        fn = e.Symbol.FamilyName.lower()
    except Exception:
        return None
    if any(k in fn for k in SLEEVE_FAMILY_KEYS):
        return "sleeve"
    if any(k in fn for k in CHIP_FAMILY_KEYS):
        return "chip"
    return None

OUT = []
if not RUN:
    OUT = ["Set IN[0]=True to run. IN[1]=True deletes (otherwise just lists)."]
else:
    views = [v for v in FilteredElementCollector(doc).OfClass(View)
             if (not v.IsTemplate) and v.Name in VIEW_NAMES]
    to_delete = []
    for view in views:
        for t in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag):
            fam = doc.GetElement(t.GetTypeId()).FamilyName
            if fam == CHIP_TAG_FAMILY:
                wrong = "sleeve"
            elif fam in (SLEEVE_TAG_FAMILY, "Sleeve-Tag-B"):
                wrong = "chip"
            else:
                continue
            try:
                kinds = [kind_of(r) for r in t.GetTaggedReferences()]
            except Exception:
                continue
            if kinds and all(k == wrong for k in kinds):
                to_delete.append(t.Id)
                OUT.append("{0} | tag {1} ({2}) is on a {3}".format(view.Name, iid(t.Id), fam, wrong))
    OUT.insert(0, "{0} wrong tags found".format(len(to_delete)))
    if DELETE and to_delete:
        TransactionManager.Instance.EnsureInTransaction(doc)
        for i in to_delete:
            doc.Delete(i)
        TransactionManager.Instance.TransactionTaskDone()
        OUT.insert(1, "DELETED {0} tags. Run tag_qc.py (Apply) to re-tag them correctly.".format(len(to_delete)))

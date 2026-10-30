"""Tiny storage demo; the experiment does not import any Azure code."""
import csv
from expops.core import process, log_metric


@process()
def summarize(context=None):
    with context.inputs["samples"].open("r", newline="") as stream:
        rows = list(csv.DictReader(stream))
    count = len(rows)
    total = sum(float(row["value"]) for row in rows)
    log_metric("rows", count)
    log_metric("total", total)
    return {"count": count, "total": total}


@process()
def verify(count=None, total=None, context=None):
    if count != 4 or total != 20:
        raise ValueError("Unexpected demo dataset")
    log_metric("verified", 1)
    return {"verified": True}

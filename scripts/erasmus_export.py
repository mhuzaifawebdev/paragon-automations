"""Downloads Erasmus+ projects from the Project Results Platform (erasmus-plus.ec.europa.eu/projects) without its
1,000-row Excel limit, through the same search service the site's own page uses, and writes one Excel file.

    python scripts/erasmus_export.py                       # the client's brief (below), ongoing projects
    python scripts/erasmus_export.py --out my.xlsx --status ongoing --years 2025,2026

Brief: KA1 learning mobility, action types KA121-SCH, KA120-SCH, KA121-VET, KA131-HED, KA120-VET, ongoing projects,
coordinators outside Malta (Malta's projects go to their own sheet). Sheets: Projects (one row per project with its
coordinator), Partners (one row per participating organisation), Coordinators (one row per organisation, its projects
and total EU grant). Read-only: it only reads public project data.
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

API = "https://ec.europa.eu/programmes/service/search/project/search"
HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (research export)",
           "Origin": "https://erasmus-plus.ec.europa.eu", "Referer": "https://erasmus-plus.ec.europa.eu/"}
# action type -> the platform's "level 3" id (read from the platform's own data)
TYPES = {"KA121-SCH": "43223892", "KA120-SCH": "43223871", "KA121-VET": "43223889", "KA131-HED": "43223907",
         "KA120-VET": "43223874"}
CARD = "https://erasmus-plus.ec.europa.eu/projects/search/details/"
PAGE = 500


def search(project, start=0, size=PAGE, aggregations=False, tries=5):
    body = {"index": "eplus2021", "keyword": "", "matchAllCountries": False, "project": project, "coordinator": {},
            "partner": {}, "organisation": {}, "from": start, "size": size}
    if aggregations:
        body["withAggregations"] = True
    for i in range(tries):
        try:
            req = urllib.request.Request(API, data=json.dumps(body).encode(), headers=HEADERS)
            return json.loads(urllib.request.urlopen(req, timeout=180).read().decode("utf-8"))
        except Exception as e:                          # the service is slow at times: wait and try again
            if i == tries - 1:
                raise
            print(f"   retry {i + 1} after: {str(e)[:80]}", flush=True)
            time.sleep(5 * (i + 1))


def day(s):
    return str(s or "")[:10]


def labels(agg_list):
    """{code: label} from an aggregation list, whatever the exact key names are."""
    out = {}
    for a in agg_list or []:
        if isinstance(a, dict):
            code = a.get("ccm2Code") or a.get("code") or a.get("id") or a.get("key")
            lab = a.get("label") or a.get("name") or a.get("value") or a.get("ccm2Label")
            if code and lab:
                out[str(code)] = str(lab)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="erasmus_KA1_ongoing_projects.xlsx")
    ap.add_argument("--status", default="ongoing", help="ongoing | finalized | all")
    ap.add_argument("--years", default="", help="call years to keep, e.g. 2025,2026 (blank = all)")
    ap.add_argument("--exclude-country", default="MT")
    ap.add_argument("--types", default=",".join(TYPES))
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    years = {y.strip() for y in a.years.split(",") if y.strip()}
    docs, otype = {}, {}
    for code in [t.strip() for t in a.types.split(",") if t.strip()]:
        project = {"projectLevel3": TYPES[code]}
        if a.status != "all":
            project["projectStatus"] = a.status
        first = search(project, 0, PAGE, aggregations=True)
        total = first["total"]
        for k in ("organisationType", "coordinatorType", "partnerType"):
            otype.update(labels((first.get("aggregations") or {}).get(k)))
        print(f"{code}: {total} projects", flush=True)
        page, start = first, 0
        while True:
            for d in page.get("projectDocuments") or []:
                docs[d["projectId"]] = d
            start += PAGE
            if start >= total:
                break
            time.sleep(0.5)
            page = search(project, start)
            print(f"   {code}: {min(start + PAGE, total)} / {total}", flush=True)

    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    proj = wb.active
    proj.title = "Projects"
    head = ["Project ID", "Action type", "Action", "Call year", "Start date", "End date", "Status", "EU grant (EUR)",
            "Project title", "Coordinator", "Coordinator PIC", "Coordinator country", "Coordinator city",
            "Coordinator type", "Coordinator website", "Partners", "Partner countries", "Project card"]
    malta = wb.create_sheet(f"Coordinator in {a.exclude_country} (excluded)")
    parts = wb.create_sheet("Partners")
    coords = wb.create_sheet("Coordinators")
    for sh in (proj, malta):
        sh.append(head)
    parts.append(["Project ID", "Action type", "Partner", "PIC", "Country", "City", "Type", "Website", "Coordinator",
                  "Project card"])
    agg = defaultdict(lambda: {"projects": 0, "grant": 0.0, "types": set(), "latest_end": "", "row": None})
    kept = skipped_year = 0
    for d in sorted(docs.values(), key=lambda x: x.get("projectName") or ""):
        if years and str(d.get("projectCallYear")) not in years:
            skipped_year += 1
            continue
        pid = d.get("projectName") or ""
        code = (re.search(r"KA\d{3}-[A-Z]{3}", pid) or [""])[0]
        c = d.get("coordinator") or {}
        partners = d.get("partners") or []
        row = [pid, code, d.get("projectLevel3Label"), d.get("projectCallYear"), day(d.get("projectStartDate")),
               day(d.get("projectEndDate")), d.get("projectStatus"), d.get("projectGrantedEuAmount"),
               d.get("projectTitle"), c.get("organisationName"), c.get("organisationPIC"), c.get("organisationCountry"),
               c.get("organisationCity"), otype.get(str(c.get("organisationType")), c.get("organisationType")),
               c.get("organisationWebsite"), len(partners),
               ", ".join(sorted({p.get("organisationCountry") or "" for p in partners} - {""})), CARD + pid]
        if (c.get("organisationCountry") or "").upper() == a.exclude_country.upper():
            malta.append(row)
            continue
        proj.append(row)
        kept += 1
        for p in partners:
            parts.append([pid, code, p.get("organisationName"), p.get("organisationPIC"), p.get("organisationCountry"),
                          p.get("organisationCity"), otype.get(str(p.get("organisationType")), p.get("organisationType")),
                          p.get("organisationWebsite"), c.get("organisationName"), CARD + pid])
        g = agg[c.get("organisationPIC") or c.get("organisationName")]
        g["projects"] += 1
        g["grant"] += float(d.get("projectGrantedEuAmount") or 0)
        g["types"].add(code)
        g["latest_end"] = max(g["latest_end"], day(d.get("projectEndDate")))
        g["row"] = [c.get("organisationName"), c.get("organisationPIC"), c.get("organisationCountry"),
                    c.get("organisationCity"), otype.get(str(c.get("organisationType")), c.get("organisationType")),
                    c.get("organisationWebsite")]
    coords.append(["Organisation", "PIC", "Country", "City", "Type", "Website", "Projects", "Total EU grant (EUR)",
                   "Action types", "Latest end date"])
    for g in sorted(agg.values(), key=lambda g: -g["grant"]):
        coords.append(g["row"] + [g["projects"], round(g["grant"], 2), ", ".join(sorted(g["types"])), g["latest_end"]])
    for sh in (proj, malta, parts, coords):
        for cell in sh[1]:
            cell.font = Font(bold=True)
        sh.freeze_panes = "A2"
    wb.save(a.out)
    print(f"\n{len(docs)} projects downloaded; {kept} written to Projects, {malta.max_row - 1} with a coordinator in "
          f"{a.exclude_country} kept apart, {skipped_year} outside the call years; {parts.max_row - 1} partner rows; "
          f"{coords.max_row - 1} coordinating organisations.\nSaved: {Path(a.out).resolve()}")


if __name__ == "__main__":
    main()

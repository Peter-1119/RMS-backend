# data_store.py
import re, json
from typing import Dict, Any, Tuple
from config import CSV_MACHINES, CSV_PROJECTS

# in-memory caches
_specifics_data: Dict[str, Any] = {}
_machines_info: Dict[str, Any] = {}
_projects_data: Dict[str, Any] = {}

def _read_projects_csv(path) -> Dict[str, Any]:
    projects = {}
    with open(path, "r", encoding="utf-8") as f:
        header = f.readline()
        for line in f:
            sc, sn, pn = line.rstrip("\n").split(",")
            sc, sn, pn = sc.strip(), sn.strip(), pn.strip()
            if not sc or not sn: continue
            pc = ""
            if pn:
                pc = pn[:3] if bool(re.match(r"^[a-zA-Z]+$", pn[:3])) else ""
                pn = pn if pc is None else pn[3:]
            else:
                pn = "其他"
            projects.setdefault(pn, {"code": pc, "specifics": []})
            projects[pn]["specifics"].append(sn)
    return projects

def _read_machines_csv(path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    specifics, machines = {}, {}
    with open(path, "r", encoding="utf-8") as f:
        header = f.readline()
        for line in f:
            sc, sn, gc, gn, mc, mn = [x.strip() for x in line.rstrip("\n").split(",")]
            specifics.setdefault(sn, {"code": sc, "groups": {}})
            specifics[sn]["groups"].setdefault(gn, {"code": gc, "machines": {}})
            specifics[sn]["groups"][gn]["machines"].setdefault(mn, {"code": mc})
            machines.setdefault(mn, {"code": mc, "specifics": []})
            if sn not in machines[mn]["specifics"]:
                machines[mn]["specifics"].append(sn)
    return specifics, machines

def load_all():
    global _specifics_data, _machines_info, _projects_data
    _specifics_data, _machines_info = _read_machines_csv(CSV_MACHINES)
    _projects_data = _read_projects_csv(CSV_PROJECTS)

def get_specifics(): return _specifics_data
def get_machines():  return _machines_info
def get_projects():  return _projects_data

"""Viridis Land Package v1: one exchange format for evidence about a piece of land.

Every Viridis tool answers one question about land. A Land Package carries the answer
between tools without losing where it came from: GeoJSON layers in WGS84, a source for
every layer, typed claims, a list of what could not be retrieved, a privacy declaration
and a content hash. It follows the conventions of the DFM Landscape Package v1
(EPSG:4326, metres, a ``sources`` object), so that format converts without loss.

Invariants
  LP1  Geometry is GeoJSON in EPSG:4326 (longitude, latitude), lengths in metres,
       areas in square metres. Out-of-range coordinates and open rings are errors.
  LP2  Provenance: every layer with features names at least one source that exists
       in ``sources``. Every claim has a kind and names a source or a method.
  LP3  Honesty: ``not_retrieved`` is always present. Claim kinds are a fixed list, so
       a modelled figure cannot travel as a measurement.
  LP4  Privacy: the package declares location precision, whether it holds personal
       data, and the consent it rests on. ``shareable`` is derived, never asserted.
  LP5  Integrity: ``integrity.content_sha256`` is the SHA-256 of the canonical JSON
       (RFC 8785) of everything except ``integrity``. A package that does not match is
       invalid. The canonical form does not depend on the language that wrote it.
  LP6  Lossless: a DFM Landscape Package converted in and back out is identical.
  LP7  Nothing is dropped silently: unknown layers and ``extensions`` are preserved;
       an unsupported geometry type is an error, not a flattening.
  LP8  Deterministic: the same content gives the same bytes and the same hash.

Standard library only, so any tool can vendor this file.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VERSION = "1.0"
CRS = "EPSG:4326"
UNITS = {"length": "m", "area": "m2"}
SUBJECT_KINDS = ("property", "field", "woodlot", "landscape")
LOCATION_PRECISION = ("exact", "generalized")
CONSENT = ("holder", "covered", "none", "not_applicable")
CLAIM_KINDS = ("measured", "modelled", "estimated", "derived", "stated")
NOT_RETRIEVED_STATUS = ("failed", "defaulted", "estimated")
GEOMETRY_TYPES = ("Point", "MultiPoint", "LineString", "MultiLineString", "Polygon", "MultiPolygon")
UNRECORDED_SOURCE = "unrecorded"
SQM_PER_ACRE = 4046.8564224
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REQUIRED = ("land_package", "name", "created", "generator", "crs", "units", "subject",
             "privacy", "sources", "layers", "claims", "not_retrieved", "integrity")
_OPTIONAL = ("receipt", "extensions")


class LandPackageError(ValueError):
    """Raised when a package cannot be built or converted without breaking an invariant."""


# ── LP5 / LP8: canonical form and hash ───────────────────────────────────────

def _number(value) -> str:
    """A number as ECMAScript writes it, so the hash is the same in any language (LP5)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("not a number")
    if isinstance(value, int):
        if abs(value) > 9007199254740991:
            raise ValueError("an integer beyond 2^53 cannot be written portably; store it as a string")
        return str(value)
    if not math.isfinite(value):
        raise ValueError("NaN and infinity cannot be written as JSON")
    if value == 0:
        return "0"
    # Shortest digits that round-trip (repr), laid out by the ECMAScript Number-to-String rules.
    text = repr(abs(value))
    mantissa, _, exponent = text.partition("e")
    whole, _, frac = mantissa.partition(".")
    digits = (whole + frac).lstrip("0")
    point = len(whole) + (int(exponent) if exponent else 0) - (len(whole + frac) - len((whole + frac).lstrip("0")))
    digits = digits.rstrip("0") or "0"
    k, n = len(digits), point
    if k <= n <= 21:
        body = digits + "0" * (n - k)
    elif 0 < n <= 21:
        body = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        body = "0." + "0" * (-n) + digits
    else:
        body = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if n - 1 > 0 else "-") + str(abs(n - 1))
    return ("-" if value < 0 else "") + body


def canonical(value: Any) -> str:
    """Canonical JSON as in RFC 8785: keys sorted by UTF-16 code units, no whitespace,
    numbers as ECMAScript writes them. Any language can reproduce it (LP5, LP8)."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        value.encode("utf-8")              # refuses lone surrogates
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical(v) for v in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(k, str) for k in value):
            raise ValueError("object keys must be strings")
        keys = sorted(value, key=lambda k: k.encode("utf-16-be", "surrogatepass"))
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical(value[k]) for k in keys) + "}"
    raise ValueError(f"{type(value).__name__} cannot be written as JSON")


def content_sha256(package: dict) -> str:
    body = {k: v for k, v in package.items() if k != "integrity"}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def seal(package: dict) -> dict:
    """Return a copy with ``integrity`` set from its content (LP5)."""
    # Through JSON and back, so tuples become lists and only JSON types remain (LP8).
    sealed = json.loads(json.dumps({k: v for k, v in package.items() if k != "integrity"},
                                   ensure_ascii=False, allow_nan=False))  # key order kept for LP6
    sealed["integrity"] = {"algorithm": "sha256", "content_sha256": content_sha256(sealed)}
    return sealed


def dumps(package: dict) -> str:
    """The bytes to write: canonical key order, indented, newline-terminated (LP8)."""
    return json.dumps(package, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


# ── LP1: geometry ────────────────────────────────────────────────────────────

def _position_errors(pos, where: str) -> List[str]:
    if (not isinstance(pos, (list, tuple)) or len(pos) < 2
            or not all(isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c) for c in pos[:3])):
        return [f"{where}: a position must be [longitude, latitude] numbers"]
    lon, lat = pos[0], pos[1]
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return [f"{where}: position {lon}, {lat} is outside longitude and latitude ranges (LP1)"]
    return []


def _line_errors(coords, where: str, minimum: int, closed: bool) -> List[str]:
    if not isinstance(coords, list) or len(coords) < minimum:
        return [f"{where}: needs at least {minimum} positions"]
    errors: List[str] = []
    for i, pos in enumerate(coords):
        errors += _position_errors(pos, f"{where}[{i}]")
    if closed and not errors and list(coords[0][:2]) != list(coords[-1][:2]):
        errors.append(f"{where}: ring is not closed (LP1)")
    return errors


def geometry_errors(geometry: Any, where: str) -> List[str]:
    if not isinstance(geometry, dict):
        return [f"{where}: geometry is missing"]
    kind, coords = geometry.get("type"), geometry.get("coordinates")
    if kind not in GEOMETRY_TYPES:
        return [f"{where}: geometry type {json.dumps(kind)} is not supported; it is not converted or dropped (LP7)"]
    if kind == "Point":
        return _position_errors(coords, where)
    if kind == "MultiPoint":
        return _line_errors(coords, where, 1, False)
    if kind == "LineString":
        return _line_errors(coords, where, 2, False)
    if not isinstance(coords, list) or not coords:
        return [f"{where}: coordinates are empty"]
    errors: List[str] = []
    if kind == "MultiLineString":
        for i, line in enumerate(coords):
            errors += _line_errors(line, f"{where}.line[{i}]", 2, False)
    elif kind == "Polygon":
        for i, ring in enumerate(coords):
            errors += _line_errors(ring, f"{where}.ring[{i}]", 4, True)
    else:
        for p, polygon in enumerate(coords):
            if not isinstance(polygon, list) or not polygon:
                errors.append(f"{where}.polygon[{p}]: coordinates are empty")
                continue
            for i, ring in enumerate(polygon):
                errors += _line_errors(ring, f"{where}.polygon[{p}].ring[{i}]", 4, True)
    return errors


# ── Validation ───────────────────────────────────────────────────────────────

def validate(package: Any) -> Tuple[List[str], List[str]]:
    """Return (errors, warnings). A package with no errors satisfies LP1 to LP5 and LP7."""
    errors: List[str] = []
    warnings: List[str] = []
    if not isinstance(package, dict):
        return ["a Land Package is a JSON object"], warnings
    for key in _REQUIRED:
        if key not in package:
            errors.append(f"missing field: {key}")
    for key in package:
        if key not in _REQUIRED and key not in _OPTIONAL:
            errors.append(f"unknown top-level field: {key} (put tool-specific content under extensions)")
    if errors:
        return errors, warnings

    if package["land_package"] != VERSION:
        errors.append(f"land_package must be {json.dumps(VERSION)}")
    if not isinstance(package["name"], str) or not package["name"].strip():
        errors.append("name must be a non-empty string")
    if not isinstance(package["created"], str) or not re.match(r"^\d{4}-\d{2}-\d{2}", package["created"]):
        errors.append("created must start with a date, YYYY-MM-DD")
    gen = package["generator"]
    if not (isinstance(gen, dict) and isinstance(gen.get("tool"), str) and gen["tool"].strip()):
        errors.append("generator.tool must name the tool that wrote the package")
    if package["crs"] != CRS:
        errors.append(f"crs must be {CRS} (LP1)")
    if package["units"] != UNITS:
        errors.append("units must be length m and area m2 (LP1)")

    subject = package["subject"]
    if not isinstance(subject, dict) or subject.get("kind") not in SUBJECT_KINDS:
        errors.append(f"subject.kind must be one of {', '.join(SUBJECT_KINDS)}")
    elif subject.get("area_m2") is not None and not (
            isinstance(subject["area_m2"], (int, float)) and not isinstance(subject["area_m2"], bool)
            and subject["area_m2"] >= 0):
        errors.append("subject.area_m2 must be a non-negative number or null")

    privacy = package["privacy"]
    if not isinstance(privacy, dict):
        errors.append("privacy must be an object (LP4)")
    else:
        if privacy.get("location") not in LOCATION_PRECISION:
            errors.append(f"privacy.location must be one of {', '.join(LOCATION_PRECISION)} (LP4)")
        if not isinstance(privacy.get("personal_data"), bool):
            errors.append("privacy.personal_data must be true or false (LP4)")
        if privacy.get("consent") not in CONSENT:
            errors.append(f"privacy.consent must be one of {', '.join(CONSENT)} (LP4)")
        if "shareable" in privacy:
            errors.append("privacy.shareable is derived by the reader and must not be written (LP4)")

    sources = package["sources"]
    if not isinstance(sources, dict):
        errors.append("sources must be an object")
        sources = {}
    for sid, record in sources.items():
        if not _SOURCE_ID.match(str(sid)):
            errors.append(f"source id {json.dumps(str(sid))} must use letters, digits, dot, dash or underscore")
        if not isinstance(record, dict) or not isinstance(record.get("title"), str) or not record["title"].strip():
            errors.append(f"source {json.dumps(str(sid))} needs a title (LP2)")

    layers = package["layers"]
    if not isinstance(layers, dict):
        errors.append("layers must be an object")
        layers = {}
    for name, layer in layers.items():
        if not _NAME.match(str(name)):
            errors.append(f"layer name {json.dumps(str(name))} must start with a letter and use letters, digits or underscore")
        if not isinstance(layer, dict) or not isinstance(layer.get("features"), list) or not isinstance(
                layer.get("sources"), list):
            errors.append(f"layer {name}: needs a sources list and a features list")
            continue
        unknown = [s for s in layer["sources"] if s not in sources]
        if unknown:
            errors.append(f"layer {name}: names sources that are not in sources: {', '.join(map(str, unknown))} (LP2)")
        if layer["features"] and not layer["sources"]:
            errors.append(f"layer {name}: has features but names no source (LP2)")
        if layer["features"] and layer["sources"] == [UNRECORDED_SOURCE]:
            warnings.append(f"layer {name}: the source was not recorded by the tool that made it")
        for i, feature in enumerate(layer["features"]):
            where = f"layer {name}[{i}]"
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                errors.append(f"{where}: must be a GeoJSON Feature")
                continue
            if not isinstance(feature.get("properties"), dict):
                errors.append(f"{where}: properties must be an object")
            errors += geometry_errors(feature.get("geometry"), where)

    claims = package["claims"]
    if not isinstance(claims, list):
        errors.append("claims must be a list")
        claims = []
    seen = set()
    for i, claim in enumerate(claims):
        where = f"claim[{i}]"
        if not isinstance(claim, dict):
            errors.append(f"{where}: must be an object")
            continue
        cid = claim.get("id")
        if not isinstance(cid, str) or not cid.strip() or cid in seen:
            errors.append(f"{where}: needs a unique id")
        seen.add(cid)
        if not isinstance(claim.get("statement"), str) or not claim["statement"].strip():
            errors.append(f"{where}: needs a statement")
        if claim.get("kind") not in CLAIM_KINDS:
            errors.append(f"{where}: kind must be one of {', '.join(CLAIM_KINDS)} (LP3)")
        named = claim.get("sources") or []
        if not isinstance(named, list) or any(s not in sources for s in named):
            errors.append(f"{where}: names a source that is not in sources (LP2)")
        if not named and not (isinstance(claim.get("method"), str) and claim["method"].strip()):
            errors.append(f"{where}: must name a source or a method (LP2)")
        if claim.get("kind") == "measured" and not named:
            errors.append(f"{where}: a measured claim must name the source of the measurement (LP3)")
        if "value" in claim and claim["value"] is not None and not (
                isinstance(claim["value"], (int, float)) and not isinstance(claim["value"], bool)
                and math.isfinite(claim["value"])):
            errors.append(f"{where}: value must be a finite number")
        if "value" in claim and claim.get("value") is not None and not claim.get("unit"):
            errors.append(f"{where}: a value needs a unit")

    missing = package["not_retrieved"]
    if not isinstance(missing, list):
        errors.append("not_retrieved must be a list, empty when everything was retrieved (LP3)")
    else:
        for i, entry in enumerate(missing):
            if not (isinstance(entry, dict) and isinstance(entry.get("source"), str) and entry["source"].strip()
                    and entry.get("status") in NOT_RETRIEVED_STATUS
                    and isinstance(entry.get("consequence"), str) and entry["consequence"].strip()):
                errors.append(f"not_retrieved[{i}]: needs source, status ({', '.join(NOT_RETRIEVED_STATUS)}) "
                              "and consequence (LP3)")

    if "extensions" in package and not isinstance(package["extensions"], dict):
        errors.append("extensions must be an object keyed by tool name")
    if "receipt" in package and not isinstance(package["receipt"], dict):
        errors.append("receipt must be an object")

    integrity = package["integrity"]
    if not (isinstance(integrity, dict) and integrity.get("algorithm") == "sha256"
            and isinstance(integrity.get("content_sha256"), str)):
        errors.append("integrity must give algorithm sha256 and content_sha256 (LP5)")
    elif not errors:
        try:
            if integrity["content_sha256"] != content_sha256(package):
                errors.append("integrity.content_sha256 does not match the content (LP5)")
        except ValueError:
            errors.append("the package holds a value that cannot be written as JSON (LP8)")
    return errors, warnings


def shareable(package: dict) -> bool:
    """LP4: may this package be shown to people beyond its holder? Derived, never stored."""
    privacy = package.get("privacy") or {}
    return privacy.get("personal_data") is False and privacy.get("consent") in ("holder", "covered", "not_applicable")


def require_valid(package: dict) -> dict:
    errors, _ = validate(package)
    if errors:
        raise LandPackageError("; ".join(errors[:6]) + (f"; and {len(errors) - 6} more" if len(errors) > 6 else ""))
    return package


# ── LP6: DFM Landscape Package v1 in and out ─────────────────────────────────

def from_dfm(dfm: dict) -> dict:
    """Wrap a DFM Landscape Package v1 as a Land Package without losing anything."""
    if not isinstance(dfm, dict) or dfm.get("dfm_package") != "1.0":
        raise LandPackageError("not a DFM Landscape Package v1 (dfm_package must be \"1.0\")")
    if dfm.get("crs") != CRS or dfm.get("units") != UNITS:
        raise LandPackageError("the DFM package is not in EPSG:4326 with metres; it is not reprojected here (LP1)")
    layers_in = dfm.get("layers")
    if not isinstance(layers_in, dict):
        raise LandPackageError("the DFM package has no layers object")
    sources = copy.deepcopy(dfm.get("sources") or {})
    if UNRECORDED_SOURCE in sources:
        raise LandPackageError(f"the DFM package already uses the reserved source id {UNRECORDED_SOURCE!r}")
    recorded = sorted(sources)
    needs_placeholder = not recorded and any(layers_in.values())
    if needs_placeholder:
        sources[UNRECORDED_SOURCE] = {"title": "Source not recorded in the DFM Landscape Package"}
    layer_sources = recorded or ([UNRECORDED_SOURCE] if needs_placeholder else [])
    layers = {name: {"sources": list(layer_sources) if features else [], "features": copy.deepcopy(features)}
              for name, features in layers_in.items()}
    parcels = layers_in.get("parcels") or []
    consents = {(f.get("properties") or {}).get("consent") for f in parcels}
    consent = "not_applicable" if not parcels else "covered" if consents == {"covered"} else "none"
    extra = {k: copy.deepcopy(v) for k, v in dfm.items() if k not in ("crs", "units", "layers")}
    extra["layer_order"] = list(layers_in)
    extra["top_level_order"] = list(dfm)
    return seal({
        "land_package": VERSION,
        "name": str(dfm.get("name") or "DFM Landscape Package"),
        "created": str(dfm.get("created") or "1970-01-01"),
        "generator": {"tool": str(dfm.get("generator") or "DFM Landscape Package v1")},
        "crs": CRS, "units": dict(UNITS),
        "subject": {"kind": "landscape", "area_m2": None},
        "privacy": {"location": "exact", "personal_data": bool(parcels), "consent": consent},
        "sources": sources,
        "layers": layers,
        "claims": [],
        "not_retrieved": [],
        "extensions": {"dfm": extra},
    })


def to_dfm(package: dict) -> dict:
    """Recover the DFM Landscape Package a Land Package was made from (LP6)."""
    require_valid(package)
    extra = (package.get("extensions") or {}).get("dfm")
    if not isinstance(extra, dict) or extra.get("dfm_package") != "1.0":
        raise LandPackageError("this Land Package was not made from a DFM Landscape Package")
    layers = {name: copy.deepcopy(package["layers"][name]["features"]) for name in extra["layer_order"]}
    extra_layers = [n for n in package["layers"] if n not in layers]
    if extra_layers:
        raise LandPackageError("layers added since conversion would be lost in DFM form: " + ", ".join(extra_layers))
    out: Dict[str, Any] = {}
    for key in extra["top_level_order"]:
        if key == "crs":
            out[key] = CRS
        elif key == "units":
            out[key] = dict(UNITS)
        elif key == "layers":
            out[key] = layers
        else:
            out[key] = copy.deepcopy(extra[key])
    return out


# ── Arboris: a land design report as a Land Package ──────────────────────────

_ARBORIS_SOURCES = {
    "arboris-engine": {"title": "Arboris design engine", "notes": "Zones, plant positions and proposed water features are model output."},
    "usgs-elevation": {"title": "USGS elevation point query service", "url": "https://epqs.nationalmap.gov/v1/json", "license": "Public domain"},
    "plant-schedule": {"title": "Arboris plant schedule for this design", "notes": "Counts come from zone area and spacing; not reviewed by a grower."},
}


def from_arboris(model: dict, spatial_features: dict, boundary_feature_collection: dict,
                 boundary_source_title: str, created: Optional[str] = None) -> dict:
    """Build the Land Package for one Arboris run from its design model and map layers."""
    def feats(kind):
        return [f for f in (spatial_features.get("features") or []) if (f.get("properties") or {}).get("type") == kind]

    def keep(feature, keys):
        props = feature.get("properties") or {}
        return {"type": "Feature", "geometry": copy.deepcopy(feature.get("geometry")),
                "properties": {k: props[k] for k in keys if k in props and props[k] not in (None, "")}}

    zone_by_id = {z["zone"]: z for z in model["zones"]}
    zones = []
    for f in feats("zone"):
        zid = (f.get("properties") or {}).get("zone")
        info = zone_by_id.get(zid, {})
        zones.append({"type": "Feature", "geometry": copy.deepcopy(f.get("geometry")),
                      "properties": {"zone": zid, "name": info.get("name", ""),
                                     "area_m2": round(float(info.get("area_acres", 0)) * SQM_PER_ACRE, 1)}})
    plants = [keep(f, ("zone", "common_name", "botanical_name", "layer")) for f in feats("plant")]
    water = [dict(keep(f, ("type", "label", "radius_m", "length_m")),) for f in feats("swale") + feats("pond")]
    for f in water:
        f["properties"]["status"] = "proposed, not engineered"
    boundary = [{"type": "Feature", "geometry": copy.deepcopy(f.get("geometry")), "properties": {}}
                for f in (boundary_feature_collection.get("features") or [])[:1]]

    sources = copy.deepcopy(_ARBORIS_SOURCES)
    sources["property-boundary"] = {"title": boundary_source_title}
    totals, receipt = model["totals"], model["receipt"]
    cost = totals["cost"]
    claims = [
        {"id": "area-total", "statement": "Area inside the boundary shown on the zone map",
         "kind": "derived", "value": round(totals["area_acres"] * SQM_PER_ACRE, 1), "unit": "m2",
         "sources": ["property-boundary"], "method": "Sum of zone polygon areas"},
        {"id": "plants-total", "statement": "Plants in the schedule, each placed on the map",
         "kind": "modelled", "value": totals["plants"], "unit": "plants",
         "sources": ["plant-schedule"], "method": "Zone area and spacing"},
        {"id": "cost-full-design", "statement": "Cost of planting every zone, excluding labour, fencing, irrigation and delivery",
         "kind": "estimated", "value": cost["total"], "unit": "USD",
         "method": f"Cost rule {receipt.get('cost_rule', 'A1')}: plants, plus 18% of plant cost for earthworks, plus 500 USD per acre of zones 1 to 3"},
    ]
    for z in model["zones"]:
        claims.append({"id": f"zone-{z['zone']}-area", "statement": f"Area of zone {z['zone']}: {z['name']}",
                       "kind": "derived", "value": round(z["area_acres"] * SQM_PER_ACRE, 1), "unit": "m2",
                       "sources": ["arboris-engine"], "method": "Zone polygon area"})
    layers = {"boundary": {"sources": ["property-boundary"], "features": boundary},
              "zones": {"sources": ["arboris-engine", "usgs-elevation"], "features": zones},
              "plants": {"sources": ["arboris-engine", "plant-schedule"], "features": plants},
              "water": {"sources": ["arboris-engine"] if water else [], "features": water}}
    return seal({
        "land_package": VERSION,
        "name": str(model["property"]["name"]),
        "created": created or str(model.get("generated") or "1970-01-01"),
        "generator": {"tool": "Arboris", "version": str(receipt.get("engine_commit", "not recorded"))},
        "crs": CRS, "units": dict(UNITS),
        "subject": {"kind": "property", "area_m2": round(totals["area_acres"] * SQM_PER_ACRE, 1)},
        # A street address identifies a household, so the package is personal data held for its owner.
        "privacy": {"location": "exact", "personal_data": True, "consent": "holder"},
        "sources": sources,
        "layers": layers,
        "claims": claims,
        "not_retrieved": [{"source": e["source"], "status": e["status"], "consequence": e["consequence"]}
                          for e in model.get("not_retrieved", [])],
        "receipt": {k: str(v) for k, v in receipt.items()},
        "extensions": {"arboris": {"decision": copy.deepcopy(model["decision"]),
                                   "zones": copy.deepcopy(model["zones"])}},
    })


def write(package: dict, path) -> Path:
    require_valid(package)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(package), encoding="utf-8")
    return path


def read(path) -> dict:
    return require_valid(json.loads(Path(path).read_text(encoding="utf-8")))


# ── Command line ─────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    """``python land_package.py validate FILE`` or ``... from-dfm IN OUT`` or ``... to-dfm IN OUT``."""
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    usage = ("usage: land_package.py validate FILE\n"
             "       land_package.py from-dfm DFM_FILE LAND_PACKAGE_FILE\n"
             "       land_package.py to-dfm LAND_PACKAGE_FILE DFM_FILE")
    try:
        if len(args) == 2 and args[0] == "validate":
            package = json.loads(Path(args[1]).read_text(encoding="utf-8"))
            errors, warnings = validate(package)
            for line in warnings:
                print(f"warning: {line}")
            for line in errors:
                print(f"error: {line}")
            if errors:
                print(f"INVALID: {len(errors)} error(s)")
                return 1
            print(f"VALID: Land Package {package['land_package']}, {len(package['layers'])} layer(s), "
                  f"{len(package['claims'])} claim(s), sha256 {package['integrity']['content_sha256'][:12]}, "
                  f"shareable: {'yes' if shareable(package) else 'no'}")
            return 0
        if len(args) == 3 and args[0] == "from-dfm":
            dfm = json.loads(Path(args[1]).read_text(encoding="utf-8"))
            write(from_dfm(dfm.get("package", dfm) if isinstance(dfm, dict) else dfm), args[2])
            print(f"wrote {args[2]}")
            return 0
        if len(args) == 3 and args[0] == "to-dfm":
            out = to_dfm(json.loads(Path(args[1]).read_text(encoding="utf-8")))
            Path(args[2]).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"wrote {args[2]}")
            return 0
    except (LandPackageError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        return 1
    print(usage)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

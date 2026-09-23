"""将 Shapefile 按指定属性列拆分为一个个独立的 GeoJSON 文件。"""
from __future__ import annotations

import csv
import json
import os
import re

from shapefile_reader import Shapefile

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

try:
    from pyproj import CRS, Transformer
except Exception:
    CRS = None
    Transformer = None

try:
    from shapely.geometry import shape as _shp_shape, mapping as _shp_mapping
    try:
        from shapely import make_valid as _make_valid
    except Exception:
        from shapely.validation import make_valid as _make_valid
except Exception:
    _shp_shape = _shp_mapping = _make_valid = None


def repair_available() -> bool:
    return _shp_shape is not None and _make_valid is not None


def _ring_area(ring) -> float:
    total = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _clean_ring(ring):
    pts = []
    for point in ring:
        point = [point[0], point[1]]
        if not pts or point != pts[-1]:
            pts.append(point)
    if len(pts) < 3:
        return None
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    if len(pts) < 4 or abs(_ring_area(pts)) <= 1e-12:
        return None
    return pts


def _clean_geometry(geom):
    if geom is None:
        return None
    gtype = geom.get("type")
    if gtype == "Polygon":
        rings = [_clean_ring(r) for r in geom["coordinates"]]
        rings = [r for r in rings if r is not None]
        if not rings:
            return None
        return {"type": "Polygon", "coordinates": rings}
    if gtype == "MultiPolygon":
        polygons = []
        for poly in geom["coordinates"]:
            cleaned = _clean_geometry({"type": "Polygon", "coordinates": poly})
            if cleaned is not None:
                polygons.append(cleaned["coordinates"])
        if not polygons:
            return None
        if len(polygons) == 1:
            return {"type": "Polygon", "coordinates": polygons[0]}
        return {"type": "MultiPolygon", "coordinates": polygons}
    return geom


def _collect_polygons(geom, out):
    gtype = geom.get("type")
    if gtype == "Polygon":
        out.append(geom["coordinates"])
    elif gtype == "MultiPolygon":
        out.extend(geom["coordinates"])
    elif gtype == "GeometryCollection":
        for sub in geom.get("geometries", []):
            _collect_polygons(sub, out)


def _repair_geometry(geom):
    if geom is None or _shp_shape is None or _make_valid is None:
        return geom
    try:
        fixed = _make_valid(_shp_shape(geom))
        if fixed.is_empty:
            return geom
        mapped = _shp_mapping(fixed)
        if mapped["type"] in ("Polygon", "MultiPolygon"):
            return mapped
        if mapped["type"] == "GeometryCollection":
            polygons = []
            _collect_polygons(mapped, polygons)
            if not polygons:
                return None
            if len(polygons) == 1:
                return {"type": "Polygon", "coordinates": polygons[0]}
            return {"type": "MultiPolygon", "coordinates": polygons}
        return geom
    except Exception:
        return geom


def _simplify_geometry(geom, tolerance):
    if geom is None or _shp_shape is None or tolerance <= 0:
        return geom
    try:
        simplified = _shp_shape(geom).simplify(tolerance, preserve_topology=True)
        if simplified.is_empty:
            return None
        return _shp_mapping(simplified)
    except Exception:
        return geom


def sanitize_filename(name, fallback: str) -> str:
    if name is None:
        name = ""
    name = _INVALID_CHARS.sub("_", str(name).strip())
    name = name.strip(" .")
    if not name:
        name = fallback
    if len(name) > 120:
        name = name[:120]
    return name


def _csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _write_attributes_csv(sf, out_dir: str, csv_name: str):
    path = os.path.join(out_dir, csv_name)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(sf.field_names)
        for idx, row in enumerate(sf.iter_records()):
            if idx >= sf.record_count:
                break
            writer.writerow([_csv_cell(row.get(name)) for name in sf.field_names])
    return path


def _round_coords(coords, nd: int):
    if isinstance(coords[0], (int, float)):
        return [round(c, nd) for c in coords]
    return [_round_coords(c, nd) for c in coords]


def _make_transformer(prj_wkt, keep_crs: bool):
    if keep_crs or not prj_wkt or CRS is None:
        return None
    try:
        src = CRS.from_wkt(prj_wkt)
        if src.equals(CRS.from_epsg(4326)):
            return None
        return Transformer.from_crs(src, CRS.from_epsg(4326), always_xy=True)
    except Exception:
        return None


def _simplify_tolerance(prj_wkt, keep_crs: bool, meters: float) -> float:
    if not meters or meters <= 0:
        return 0.0
    geographic = True
    if CRS is not None and prj_wkt:
        try:
            target = CRS.from_wkt(prj_wkt) if keep_crs else CRS.from_epsg(4326)
            geographic = target.is_geographic
        except Exception:
            geographic = True
    return meters / 111320.0 if geographic else meters


def _transform_geometry(geom, transformer):
    if geom is None:
        return None
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    t = transformer.transform
    if gtype == "Point":
        return {"type": "Point", "coordinates": list(t(coords[0], coords[1]))}
    if gtype == "MultiPoint":
        return {"type": "MultiPoint", "coordinates": [list(t(x, y)) for x, y in coords]}
    if gtype == "LineString":
        return {"type": "LineString", "coordinates": [list(t(x, y)) for x, y in coords]}
    if gtype == "MultiLineString":
        return {"type": "MultiLineString",
                "coordinates": [[list(t(x, y)) for x, y in line] for line in coords]}
    if gtype == "Polygon":
        return {"type": "Polygon",
                "coordinates": [[list(t(x, y)) for x, y in ring] for ring in coords]}
    if gtype == "MultiPolygon":
        return {"type": "MultiPolygon",
                "coordinates": [[[list(t(x, y)) for x, y in ring] for ring in poly]
                                for poly in coords]}
    return geom


def list_columns(shp_path: str):
    return Shapefile(shp_path).field_names


def convert(shp_path: str, column: str, out_dir: str, precision: int = 6,
            keep_crs: bool = False, repair: bool = True, simplify_meters: float = 0.0,
            export_csv: bool = True, progress=None, log=None):
    if not os.path.exists(shp_path):
        raise FileNotFoundError("找不到文件：%s" % shp_path)
    sf = Shapefile(shp_path)
    if column not in sf.field_names:
        raise ValueError("属性表中不存在列：%s" % column)

    os.makedirs(out_dir, exist_ok=True)
    transformer = _make_transformer(sf.prj_wkt, keep_crs)
    do_repair = repair and repair_available()
    tolerance = _simplify_tolerance(sf.prj_wkt, keep_crs, simplify_meters)
    do_simplify = tolerance > 0 and _shp_shape is not None
    total = len(sf)
    used = {}
    written = 0
    files = []

    if log:
        mode = "保持原始坐标系" if transformer is None else "转换为 WGS84"
        log("坐标系：%s" % mode)
        log("记录数：%d，拆分列：%s" % (total, column))
        if do_simplify:
            log("几何简化容差：%g 米" % simplify_meters)
        elif simplify_meters and simplify_meters > 0:
            log("提示：未安装 shapely，跳过几何简化。")
        if repair and not repair_available():
            log("提示：未安装 shapely，跳过无效几何修复。")

    records = sf.iter_records()
    for idx, geom in enumerate(sf.iter_shapes()):
        try:
            props = next(records)
        except StopIteration:
            props = {}
        if geom is None:
            if log:
                log("跳过第 %d 条记录：无几何。" % (idx + 1))
            continue

        if transformer is not None:
            geom = _transform_geometry(geom, transformer)
        if do_simplify:
            geom = _simplify_geometry(geom, tolerance)
        if geom is None:
            if log:
                log("跳过第 %d 条记录：简化后为空。" % (idx + 1))
            continue
        if precision is not None:
            geom["coordinates"] = _round_coords(geom["coordinates"], precision)
        geom = _clean_geometry(geom)
        if do_repair:
            geom = _repair_geometry(geom)
        if geom is None:
            if log:
                log("跳过第 %d 条记录：几何退化后为空。" % (idx + 1))
            continue

        base = sanitize_filename(props.get(column), "feature_%d" % (idx + 1))
        used[base] = used.get(base, 0) + 1
        filename = base + ".geojson" if used[base] == 1 else "%s_%d.geojson" % (base, used[base])

        data = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": props,
                "geometry": geom,
            }],
        }
        with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        written += 1
        files.append(filename)
        if log:
            log("已生成 %s" % filename)
        if progress and (idx % 20 == 0 or idx + 1 == total):
            progress(idx + 1, total)

    csv_path = None
    if export_csv and sf.field_names:
        csv_name = os.path.splitext(os.path.basename(shp_path))[0] + ".csv"
        csv_path = _write_attributes_csv(sf, out_dir, csv_name)
        if log:
            log("已生成属性表 %s" % csv_name)

    if progress:
        progress(total, total)
    if log:
        log("完成：共 %d 条记录，生成 %d 个 GeoJSON 文件。" % (total, written))
    return {"total": total, "written": written, "out_dir": out_dir,
            "csv_path": csv_path, "files": files}

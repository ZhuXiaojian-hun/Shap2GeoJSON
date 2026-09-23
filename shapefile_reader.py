"""Shapefile 读取器（纯标准库实现，无第三方依赖）。

可读取 .shp 几何、.dbf 属性表、.prj 坐标系，并直接产出 GeoJSON 几何对象。
"""
from __future__ import annotations

import os
import struct

_ENCODINGS = ("utf-8", "gbk", "latin-1")


class ShapefileError(Exception):
    """Shapefile 读取异常。"""


def _decode(raw: bytes) -> str:
    raw = raw.rstrip(b"\x00").strip()
    if not raw:
        return ""
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _signed_area(ring) -> float:
    total = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _point_in_ring(x, y, ring) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def _normalize_ring(ring):
    pts = []
    for point in ring:
        if not pts or point != pts[-1]:
            pts.append(point)
    if len(pts) < 3:
        return None
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    if len(pts) < 4:
        return None
    return pts


def _rings_to_geometry(rings):
    cleaned = []
    for ring in rings:
        normalized = _normalize_ring(ring)
        if normalized is not None and abs(_signed_area(normalized)) > 1e-14:
            cleaned.append(normalized)
    if not cleaned:
        return None
    outers = []
    holes = []
    for ring in cleaned:
        if _signed_area(ring) < 0:
            outers.append({"ring": ring, "holes": []})
        else:
            holes.append(ring)
    if not outers:
        outers = [{"ring": r, "holes": []} for r in cleaned]
        holes = []
    for hole in holes:
        best = None
        best_area = None
        px, py = hole[0]
        for outer in outers:
            if _point_in_ring(px, py, outer["ring"]):
                area = abs(_signed_area(outer["ring"]))
                if best is None or area < best_area:
                    best = outer
                    best_area = area
        if best is not None:
            best["holes"].append(hole)
        else:
            outers.append({"ring": hole, "holes": []})
    polygons = [[o["ring"]] + o["holes"] for o in outers]
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def _field_value(chunk: bytes, ftype: str, decimal: int):
    text = chunk.rstrip(b"\x00")
    if ftype in ("N", "F"):
        s = text.decode("latin-1").strip()
        if s in ("", "*", "-", "."):
            return None
        try:
            if ftype == "N" and decimal == 0:
                return int(float(s))
            return float(s)
        except ValueError:
            return None
    if ftype == "L":
        c = text.strip().decode("latin-1")
        if c in ("T", "t", "Y", "y"):
            return True
        if c in ("F", "f", "N", "n"):
            return False
        return None
    if ftype == "D":
        s = text.strip().decode("latin-1")
        if len(s) == 8 and s.isdigit():
            return "%s-%s-%s" % (s[0:4], s[4:6], s[6:8])
        return s or None
    return _decode(chunk)


class Shapefile:
    """读取单个 Shapefile（.shp / .dbf / .prj）。"""

    def __init__(self, path: str):
        base, ext = os.path.splitext(path)
        if ext.lower() == ".shp":
            self.shp_path = path
        else:
            self.shp_path = path + ".shp"
        base = os.path.splitext(self.shp_path)[0]
        self.dbf_path = base + ".dbf"
        self.prj_path = base + ".prj"

        if not os.path.exists(self.shp_path):
            raise ShapefileError("找不到 SHP 文件：%s" % self.shp_path)

        self.shape_type = None
        self.bbox = None
        self.fields = []
        self.field_names = []
        self.prj_wkt = None
        self._record_count = 0
        self._dbf_header_len = 0
        self._dbf_record_len = 0

        self._read_shp_header()
        self._read_dbf_header()
        self._read_prj()

    def _read_shp_header(self):
        with open(self.shp_path, "rb") as f:
            header = f.read(100)
        if len(header) < 100:
            raise ShapefileError("SHP 文件头不完整")
        file_code = struct.unpack(">i", header[0:4])[0]
        if file_code != 9994:
            raise ShapefileError("无效的 SHP 文件（文件标识码 %d）" % file_code)
        self.shape_type = struct.unpack("<i", header[32:36])[0]
        self.bbox = struct.unpack("<4d", header[36:68])

    def _read_dbf_header(self):
        self.fields = []
        self.field_names = []
        if not os.path.exists(self.dbf_path):
            return
        with open(self.dbf_path, "rb") as f:
            header = f.read(32)
            if len(header) < 32:
                return
            self._record_count = struct.unpack("<i", header[4:8])[0]
            header_len = struct.unpack("<h", header[8:10])[0]
            self._dbf_header_len = header_len
            self._dbf_record_len = struct.unpack("<h", header[10:12])[0]
            num_fields = max(0, (header_len - 33) // 32)
            for _ in range(num_fields):
                fd = f.read(32)
                if len(fd) < 32:
                    break
                name = _decode(fd[0:11])
                ftype = chr(fd[11]) if fd[11] else "C"
                self.fields.append({
                    "name": name,
                    "type": ftype,
                    "length": fd[16],
                    "decimal": fd[17],
                })
                self.field_names.append(name)

    def _read_prj(self):
        if os.path.exists(self.prj_path):
            with open(self.prj_path, "r", encoding="utf-8", errors="replace") as f:
                self.prj_wkt = f.read().strip() or None

    def _parse_shape(self, content: bytes):
        stype = struct.unpack("<i", content[0:4])[0]
        if stype == 0:
            return None
        if stype in (1, 11, 21):
            x, y = struct.unpack("<2d", content[4:20])
            return {"type": "Point", "coordinates": [x, y]}
        if stype in (8, 18, 28):
            num = struct.unpack("<i", content[36:40])[0]
            pts = struct.unpack("<%dd" % (2 * num), content[40:40 + 16 * num])
            coords = [[pts[2 * i], pts[2 * i + 1]] for i in range(num)]
            return {"type": "MultiPoint", "coordinates": coords}
        if stype in (3, 5, 13, 15, 23, 25):
            return self._parse_parts(content, stype in (5, 15, 25))
        return None

    def _parse_parts(self, content: bytes, is_polygon: bool):
        num_parts, num_points = struct.unpack("<2i", content[36:44])
        pos = 44
        parts = struct.unpack("<%di" % num_parts, content[pos:pos + 4 * num_parts])
        pos += 4 * num_parts
        pts = struct.unpack("<%dd" % (2 * num_points), content[pos:pos + 16 * num_points])
        points = [(pts[2 * i], pts[2 * i + 1]) for i in range(num_points)]
        rings = []
        for i in range(num_parts):
            start = parts[i]
            end = parts[i + 1] if i + 1 < num_parts else num_points
            rings.append(points[start:end])
        if is_polygon:
            return _rings_to_geometry(rings)
        lines = [[list(pt) for pt in ring] for ring in rings if len(ring) >= 2]
        if not lines:
            return None
        if len(lines) == 1:
            return {"type": "LineString", "coordinates": lines[0]}
        return {"type": "MultiLineString", "coordinates": lines}

    def _parse_record(self, raw: bytes) -> dict:
        row = {}
        pos = 1
        for fld in self.fields:
            length = fld["length"]
            chunk = raw[pos:pos + length]
            pos += length
            row[fld["name"]] = _field_value(chunk, fld["type"], fld["decimal"])
        return row

    def iter_shapes(self):
        with open(self.shp_path, "rb") as f:
            f.seek(100)
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                _, content_words = struct.unpack(">2i", hdr)
                content = f.read(content_words * 2)
                if len(content) < content_words * 2:
                    break
                yield self._parse_shape(content)

    def iter_records(self):
        if not os.path.exists(self.dbf_path) or self._dbf_record_len == 0:
            while True:
                yield {}
        with open(self.dbf_path, "rb") as f:
            f.seek(self._dbf_header_len)
            for _ in range(self._record_count):
                raw = f.read(self._dbf_record_len)
                if len(raw) < self._dbf_record_len:
                    break
                yield self._parse_record(raw)

    @property
    def record_count(self) -> int:
        return self._record_count

    def __len__(self) -> int:
        return self._record_count

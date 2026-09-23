# -*- coding: utf-8 -*-
"""生成自包含的离线预览页 preview.html（纯 HTML/Canvas，无需联网或第三方库）。"""
from __future__ import annotations

import glob
import json
import os


def _load_features(out_dir, files):
    if not files:
        files = sorted(os.path.basename(p)
                       for p in glob.glob(os.path.join(out_dir, "*.geojson")))
    features = []
    for filename in files:
        path = os.path.join(out_dir, filename)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("features") or []
        if not items:
            continue
        feature = items[0]
        features.append({
            "name": os.path.splitext(filename)[0],
            "props": feature.get("properties") or {},
            "geometry": feature.get("geometry"),
        })
    return features


def build_preview(out_dir, files, column, title=None, log=None):
    features = _load_features(out_dir, files)
    title = title or "GeoJSON 预览"
    html = _render(features, column, title)
    path = os.path.join(out_dir, "preview.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    if log:
        log("已生成预览页 preview.html（%d 个要素）" % len(features))
    return path


def _render(features, column, title):
    def dump(obj):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    return _TEMPLATE.replace("__TITLE__", _escape(title)) \
                    .replace("__TITLE_JSON__", dump(title)) \
                    .replace("__COLUMN_JSON__", dump(column or "")) \
                    .replace("__COUNT__", str(len(features))) \
                    .replace("__DATA_JSON__", dump(features))


def _escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; font-family: "Microsoft YaHei", "Segoe UI", sans-serif; }
  #app { display: flex; flex-direction: column; height: 100%; }
  header { padding: 8px 14px; background: #22303f; color: #fff; display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  header .t { font-size: 15px; font-weight: 600; }
  header .meta { font-size: 12px; opacity: .8; }
  #main { flex: 1; display: flex; min-height: 0; }
  aside { width: 250px; border-right: 1px solid #d9dee5; background: #fafbfc; display: flex; flex-direction: column; }
  aside .search { padding: 8px; border-bottom: 1px solid #e6eaef; }
  aside .search input { width: 100%; padding: 5px 8px; border: 1px solid #cdd5de; border-radius: 4px; }
  #list { flex: 1; overflow: auto; }
  #list .item { display: flex; align-items: center; gap: 8px; padding: 5px 10px; cursor: pointer; font-size: 13px; border-bottom: 1px solid #f0f2f5; }
  #list .item:hover { background: #eef4fb; }
  #list .item.active { background: #dceafc; font-weight: 600; }
  #list .dot { width: 11px; height: 11px; border-radius: 50%; flex: 0 0 auto; }
  #canvasWrap { flex: 1; position: relative; min-width: 0; }
  canvas { display: block; cursor: grab; }
  #props { position: absolute; right: 10px; top: 10px; width: 300px; max-height: calc(100% - 20px);
           overflow: auto; background: rgba(255,255,255,.96); border: 1px solid #cbd3dc; border-radius: 6px;
           box-shadow: 0 4px 16px rgba(0,0,0,.15); padding: 10px 12px; font-size: 13px; display: none; }
  #props h3 { margin: 0 0 8px; font-size: 15px; }
  #props table { width: 100%; border-collapse: collapse; }
  #props th, #props td { border-bottom: 1px solid #eef1f4; padding: 3px 6px; text-align: left; vertical-align: top; word-break: break-all; }
  #props th { color: #5b6b7c; font-weight: 500; width: 40%; }
  #props .close { float: right; cursor: pointer; color: #8a97a5; }
  #tip { position: fixed; pointer-events: none; background: rgba(25,35,45,.9); color: #fff; padding: 3px 8px;
         border-radius: 4px; font-size: 12px; display: none; z-index: 10; }
  button { padding: 4px 10px; border: 1px solid #cdd5de; background: #fff; border-radius: 4px; cursor: pointer; font-size: 13px; }
  button:hover { background: #eef4fb; }
  .empty { padding: 30px; color: #8a97a5; }
</style>
</head>
<body>
<div id="app">
  <header>
    <span class="t" id="title"></span>
    <span class="meta" id="meta"></span>
    <span style="flex:1"></span>
    <button id="fit">复位视图</button>
  </header>
  <div id="main">
    <aside>
      <div class="search"><input id="search" type="text" placeholder="筛选名称..."></div>
      <div id="list"></div>
    </aside>
    <div id="canvasWrap">
      <canvas id="cv"></canvas>
      <div id="props"></div>
    </div>
  </div>
</div>
<div id="tip"></div>
<script>
"use strict";
var TITLE = __TITLE_JSON__;
var COLUMN = __COLUMN_JSON__;
var DATA = __DATA_JSON__;

var canvas = document.getElementById("cv");
var ctx = canvas.getContext("2d");
var tip = document.getElementById("tip");
var propsBox = document.getElementById("props");
var view = { scale: 1, ox: 0, oy: 0 };
var dpr = window.devicePixelRatio || 1;
var planar = false;
var hoverIndex = -1, selected = -1;
var dragStart = null, dragMoved = false, downPt = null;

document.getElementById("title").textContent = TITLE;
document.getElementById("meta").textContent = "共 " + DATA.length + " 个要素" +
  (COLUMN ? "　拆分列：" + COLUMN : "") + "　（滚轮缩放 / 拖拽平移 / 点击查看属性）";

function mercY(lat) { var r = lat * Math.PI / 180; return Math.log(Math.tan(Math.PI / 4 + r / 2)) * 180 / Math.PI; }
function projX(x) { return x; }
function projY(y) { return planar ? y : mercY(y); }

(function prepare() {
  var i, j, k, p;
  for (i = 0; i < DATA.length; i++) {
    var rings = [];
    var g = DATA[i].geometry;
    if (g && g.type === "Polygon") {
      for (j = 0; j < g.coordinates.length; j++) rings.push(g.coordinates[j]);
    } else if (g && g.type === "MultiPolygon") {
      for (j = 0; j < g.coordinates.length; j++)
        for (k = 0; k < g.coordinates[j].length; k++) rings.push(g.coordinates[j][k]);
    }
    for (j = 0; j < rings.length; j++)
      for (k = 0; k < rings[j].length; k++) {
        p = rings[j][k];
        if (Math.abs(p[0]) > 180) planar = true;
      }
  }
  for (i = 0; i < DATA.length; i++) {
    var polys = [], gg = DATA[i].geometry;
    function conv(poly) {
      var out = [];
      for (var r = 0; r < poly.length; r++) {
        var ring = [];
        for (var q = 0; q < poly[r].length; q++)
          ring.push([projX(poly[r][q][0]), projY(poly[r][q][1])]);
        out.push(ring);
      }
      return out;
    }
    if (gg && gg.type === "Polygon") polys = [conv(gg.coordinates)];
    else if (gg && gg.type === "MultiPolygon")
      for (var m = 0; m < gg.coordinates.length; m++) polys.push(conv(gg.coordinates[m]));
    DATA[i].polys = polys;
    DATA[i].bounds = cbounds(polys);
    DATA[i].rgb = hsl2rgb((i * 137.508) % 360, 0.62, 0.55);
  }
})();

function cbounds(polys) {
  var b = { minx: Infinity, miny: Infinity, maxx: -Infinity, maxy: -Infinity };
  for (var i = 0; i < polys.length; i++)
    for (var j = 0; j < polys[i].length; j++)
      for (var k = 0; k < polys[i][j].length; k++) {
        var p = polys[i][j][k];
        if (p[0] < b.minx) b.minx = p[0];
        if (p[0] > b.maxx) b.maxx = p[0];
        if (p[1] < b.miny) b.miny = p[1];
        if (p[1] > b.maxy) b.maxy = p[1];
      }
  return b;
}

function allBounds() {
  var b = { minx: Infinity, miny: Infinity, maxx: -Infinity, maxy: -Infinity };
  for (var i = 0; i < DATA.length; i++) {
    var f = DATA[i].bounds;
    if (f.minx < b.minx) b.minx = f.minx;
    if (f.miny < b.miny) b.miny = f.miny;
    if (f.maxx > b.maxx) b.maxx = f.maxx;
    if (f.maxy > b.maxy) b.maxy = f.maxy;
  }
  return b;
}

function fitBounds(b) {
  var P = 30, dx = (b.maxx - b.minx) || 1e-9, dy = (b.maxy - b.miny) || 1e-9;
  var s = Math.min((canvas.width - 2 * P) / dx, (canvas.height - 2 * P) / dy);
  view.scale = s;
  view.ox = (canvas.width - dx * s) / 2 - b.minx * s;
  view.oy = (canvas.height - dy * s) / 2 + b.maxy * s;
}

function fitAll() { fitBounds(allBounds()); draw(); }

function hsl2rgb(h, s, l) {
  h /= 360;
  var r, g, b;
  function hue(p, q, t) {
    if (t < 0) t += 1; if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  }
  if (s === 0) { r = g = b = l; }
  else {
    var q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
    r = hue(p, q, h + 1 / 3); g = hue(p, q, h); b = hue(p, q, h - 1 / 3);
  }
  return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
}
function rgba(c, a) { return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a + ")"; }

function buildPath(poly) {
  var path = new Path2D();
  for (var j = 0; j < poly.length; j++) {
    var ring = poly[j];
    for (var k = 0; k < ring.length; k++) {
      var x = view.ox + ring[k][0] * view.scale;
      var y = view.oy - ring[k][1] * view.scale;
      if (k === 0) path.moveTo(x, y); else path.lineTo(x, y);
    }
    path.closePath();
  }
  return path;
}

function draw() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#f7f8fa";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (!DATA.length) {
    ctx.fillStyle = "#8a97a5";
    ctx.font = "16px 'Microsoft YaHei', sans-serif";
    ctx.fillText("没有可预览的要素", 30, 40);
    return;
  }
  for (var i = 0; i < DATA.length; i++) {
    var f = DATA[i], c = f.rgb;
    var isHover = i === hoverIndex, isSel = i === selected;
    f._paths = [];
    for (var j = 0; j < f.polys.length; j++) {
      var path = buildPath(f.polys[j]);
      f._paths.push(path);
      ctx.fillStyle = rgba(c, isSel ? 0.8 : isHover ? 0.72 : 0.5);
      ctx.fill(path, "evenodd");
      ctx.lineWidth = (isSel || isHover) ? 1.8 : 0.7;
      ctx.strokeStyle = isSel ? "#d0021b" : "rgba(50,60,70,0.75)";
      ctx.stroke(path);
    }
  }
}

function mouse(ev) {
  var rect = canvas.getBoundingClientRect();
  return [(ev.clientX - rect.left) * dpr, (ev.clientY - rect.top) * dpr];
}

function hitTest(pt) {
  for (var i = DATA.length - 1; i >= 0; i--) {
    var paths = DATA[i]._paths || [];
    for (var j = 0; j < paths.length; j++)
      if (ctx.isPointInPath(paths[j], pt[0], pt[1])) return i;
  }
  return -1;
}

canvas.addEventListener("mousemove", function (e) {
  if (dragStart) {
    var m = mouse(e);
    view.ox += m[0] - dragStart[0];
    view.oy += m[1] - dragStart[1];
    dragStart = m;
    if (Math.abs(m[0] - downPt[0]) + Math.abs(m[1] - downPt[1]) > 4) dragMoved = true;
    draw();
    return;
  }
  var pt = mouse(e);
  var hit = hitTest(pt);
  if (hit !== hoverIndex) { hoverIndex = hit; draw(); }
  if (hit >= 0) {
    tip.style.display = "block";
    tip.textContent = DATA[hit].name;
    tip.style.left = (e.clientX + 12) + "px";
    tip.style.top = (e.clientY + 12) + "px";
    canvas.style.cursor = "pointer";
  } else {
    tip.style.display = "none";
    canvas.style.cursor = "grab";
  }
});

canvas.addEventListener("mousedown", function (e) { dragStart = mouse(e); downPt = dragStart; dragMoved = false; });
window.addEventListener("mouseup", function () { dragStart = null; });

canvas.addEventListener("click", function () {
  if (dragMoved) return;
  if (hoverIndex >= 0) { selected = hoverIndex; selectFeature(hoverIndex); draw(); markList(); }
});

canvas.addEventListener("wheel", function (e) {
  e.preventDefault();
  var pt = mouse(e);
  var wx = (pt[0] - view.ox) / view.scale;
  var wy = (view.oy - pt[1]) / view.scale;
  var factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
  view.scale *= factor;
  view.ox = pt[0] - wx * view.scale;
  view.oy = pt[1] + wy * view.scale;
  draw();
}, { passive: false });

document.getElementById("fit").addEventListener("click", fitAll);

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function selectFeature(i) {
  var f = DATA[i], html = '<span class="close">×</span>';
  html += "<h3>" + esc(f.name) + "</h3><table>";
  for (var k in f.props) {
    var v = f.props[k];
    html += "<tr><th>" + esc(k) + "</th><td>" + esc(v === null || v === undefined ? "" : v) + "</td></tr>";
  }
  html += "</table>";
  propsBox.innerHTML = html;
  propsBox.style.display = "block";
  var close = propsBox.querySelector(".close");
  if (close) close.addEventListener("click", function () { propsBox.style.display = "none"; });
}

function focusFeature(i) {
  selected = i;
  fitBounds(DATA[i].bounds);
  draw();
  selectFeature(i);
  markList();
}

var listEl = document.getElementById("list");
function buildList() {
  listEl.innerHTML = "";
  DATA.forEach(function (f, i) {
    var d = document.createElement("div");
    d.className = "item";
    d.dataset.i = i;
    d.innerHTML = '<span class="dot" style="background:' + rgba(f.rgb, 0.9) + '"></span>' + esc(f.name);
    d.addEventListener("click", function () { focusFeature(i); });
    listEl.appendChild(d);
  });
}
function markList() {
  var items = listEl.querySelectorAll(".item");
  for (var i = 0; i < items.length; i++)
    items[i].className = "item" + (Number(items[i].dataset.i) === selected ? " active" : "");
}
document.getElementById("search").addEventListener("input", function (e) {
  var q = e.target.value.trim().toLowerCase();
  var items = listEl.querySelectorAll(".item");
  for (var i = 0; i < items.length; i++) {
    var name = DATA[Number(items[i].dataset.i)].name.toLowerCase();
    items[i].style.display = (!q || name.indexOf(q) >= 0) ? "flex" : "none";
  }
});

function resize() {
  var wrap = document.getElementById("canvasWrap");
  dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(wrap.clientWidth * dpr));
  canvas.height = Math.max(1, Math.floor(wrap.clientHeight * dpr));
  canvas.style.width = wrap.clientWidth + "px";
  canvas.style.height = wrap.clientHeight + "px";
  fitAll();
}

window.addEventListener("resize", resize);
buildList();
resize();
</script>
</body>
</html>
"""

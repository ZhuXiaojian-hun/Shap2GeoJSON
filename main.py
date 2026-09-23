# -*- coding: utf-8 -*-
"""Shap2GeoJSON 图形界面：将 Shapefile 按属性列拆分为独立 GeoJSON 文件。"""
from __future__ import annotations

import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from shapefile_reader import Shapefile
import converter
import preview


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Shap2GeoJSON - 行政区划拆分工具")
        self.geometry("800x700")
        self.minsize(720, 600)
        self.shp_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.sep_var = tk.StringVar(value="_")
        self.name_preview_var = tk.StringVar(value="示例：（请选择列）")
        self.prec_var = tk.IntVar(value=6)
        self.simplify_var = tk.IntVar(value=100)
        self.wgs_var = tk.BooleanVar(value=True)
        self.repair_var = tk.BooleanVar(value=True)
        self.csv_var = tk.BooleanVar(value=True)
        self.preview_var = tk.BooleanVar(value=True)
        self.msg_queue = queue.Queue()
        self.sf = None
        self.worker = None
        self.col_map = {}
        self.col_labels = []
        self._sample = {}
        self.preview_path = None
        self._build_ui()
        self.after(120, self._poll)

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}
        frm = ttk.Frame(self)
        frm.pack(fill="both", expand=True, padx=12, pady=12)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="输入 Shapefile：").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.shp_var).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="浏览...", command=self._pick_shp).grid(row=0, column=2, **pad)

        ttk.Label(frm, text="输出名（多选列）：").grid(row=1, column=0, sticky="nw", **pad)
        colfrm = ttk.Frame(frm)
        colfrm.grid(row=1, column=1, columnspan=2, sticky="ew", **pad)
        colfrm.columnconfigure(0, weight=1)
        self.col_list = tk.Listbox(colfrm, selectmode="extended", height=6,
                                   exportselection=False, activestyle="none")
        self.col_list.grid(row=0, column=0, sticky="ew")
        col_sb = ttk.Scrollbar(colfrm, command=self.col_list.yview)
        col_sb.grid(row=0, column=1, sticky="ns")
        self.col_list.configure(yscrollcommand=col_sb.set)
        self.col_list.bind("<<ListboxSelect>>", lambda _e: self._update_name_preview())
        sub = ttk.Frame(colfrm)
        sub.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(sub, text="分隔符：").pack(side="left")
        ttk.Entry(sub, width=4, textvariable=self.sep_var).pack(side="left", padx=(0, 12))
        ttk.Label(sub, textvariable=self.name_preview_var, foreground="#33475b").pack(side="left")
        self.sep_var.trace_add("write", lambda *_a: self._update_name_preview())

        ttk.Label(frm, text="输出目录：").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.out_var).grid(row=2, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="浏览...", command=self._pick_out).grid(row=2, column=2, **pad)

        opt = ttk.Frame(frm)
        opt.grid(row=3, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(opt, text="坐标小数位：").pack(side="left")
        ttk.Spinbox(opt, from_=0, to=15, width=5, textvariable=self.prec_var).pack(
            side="left", padx=(0, 16))
        ttk.Checkbutton(opt, text="输出为 WGS84（必要时自动转换）",
                        variable=self.wgs_var).pack(side="left")

        simp = ttk.Frame(frm)
        simp.grid(row=4, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(simp, text="几何简化(米)：").pack(side="left")
        ttk.Spinbox(simp, from_=0, to=100000, increment=10, width=7,
                    textvariable=self.simplify_var).pack(side="left", padx=(0, 8))
        ttk.Label(simp, text="预设：").pack(side="left")
        for label, value in (("不简化", 0), ("50", 50), ("100", 100),
                             ("200", 200), ("500", 500)):
            ttk.Button(simp, text=label, width=6,
                       command=lambda v=value: self.simplify_var.set(v)).pack(
                side="left", padx=2)
        ttk.Label(simp, text="0=不简化，省级数据 100 米肉眼基本无损").pack(
            side="left", padx=(8, 0))

        opt2 = ttk.Frame(frm)
        opt2.grid(row=5, column=0, columnspan=3, sticky="w", **pad)
        repair_cb = ttk.Checkbutton(
            opt2, text="修复无效几何（自相交、退化环等，需 shapely）",
            variable=self.repair_var)
        repair_cb.pack(side="left")
        ttk.Checkbutton(opt2, text="同时导出属性表 CSV",
                        variable=self.csv_var).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(opt2, text="生成预览页并自动打开",
                        variable=self.preview_var).pack(side="left", padx=(16, 0))
        if not converter.repair_available():
            self.repair_var.set(False)
            repair_cb.state(["disabled"])

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=3, sticky="w", **pad)
        self.convert_btn = ttk.Button(btns, text="开始转换", command=self._start)
        self.convert_btn.pack(side="left")
        ttk.Button(btns, text="打开输出目录", command=self._open_out).pack(side="left", padx=8)
        ttk.Button(btns, text="预览输出", command=self._open_preview).pack(side="left", padx=0)

        self.progress = ttk.Progressbar(frm, mode="determinate")
        self.progress.grid(row=7, column=0, columnspan=3, sticky="ew", **pad)

        ttk.Label(frm, text="日志：").grid(row=8, column=0, sticky="nw", **pad)
        logfrm = ttk.Frame(frm)
        logfrm.grid(row=8, column=1, columnspan=2, sticky="nsew", **pad)
        frm.rowconfigure(8, weight=1)
        logfrm.columnconfigure(0, weight=1)
        logfrm.rowconfigure(0, weight=1)
        self.log = tk.Text(logfrm, height=14, state="disabled", wrap="word")
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logfrm, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)

    def _pick_shp(self):
        path = filedialog.askopenfilename(
            title="选择 Shapefile",
            filetypes=[("Shapefile", "*.shp"), ("所有文件", "*.*")],
        )
        if path:
            self.shp_var.set(path)
            self._load_shp()

    def _load_shp(self):
        path = self.shp_var.get().strip().strip('"')
        if not path:
            return
        try:
            self.sf = Shapefile(path)
        except Exception as exc:
            self.sf = None
            messagebox.showerror("读取失败", str(exc))
            return
        sample = next(iter(self.sf.iter_records()), {}) or {}
        self._sample = sample
        self.col_map = {}
        labels = []
        for name in self.sf.field_names:
            label = "%s    （示例：%s）" % (name, self._format_sample(sample.get(name)))
            if label in self.col_map:
                label = "%s #%d" % (label, len(labels) + 1)
            self.col_map[label] = name
            labels.append(label)
        self.col_labels = labels
        self.col_list.delete(0, "end")
        for label in labels:
            self.col_list.insert("end", label)
        guessed = self._guess_column(self.sf.field_names)
        default_index = next((i for i, lb in enumerate(labels)
                              if self.col_map[lb] == guessed), 0 if labels else -1)
        if default_index >= 0:
            self.col_list.selection_set(default_index)
            self.col_list.see(default_index)
        self._update_name_preview()
        if not self.out_var.get().strip():
            base = os.path.splitext(os.path.basename(path))[0]
            self.out_var.set(os.path.join(os.path.dirname(path), base + "_geojson"))
        self._clear_log()
        self._log("已加载：%s" % os.path.basename(path))
        self._log("几何类型：%s，记录数：%d" % (self.sf.shape_type, len(self.sf)))
        self._log("属性列：%s" % "、".join(self.sf.field_names))

    def _selected_columns(self):
        return [self.col_map[self.col_labels[i]] for i in self.col_list.curselection()]

    def _update_name_preview(self):
        columns = self._selected_columns()
        if not columns:
            self.name_preview_var.set("示例：（请选择列）")
            return
        separator = self.sep_var.get()
        parts = []
        for name in columns:
            value = self._sample.get(name)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                parts.append(text)
        base = converter.sanitize_filename(separator.join(parts), "feature_1")
        self.name_preview_var.set("示例：" + base + ".geojson")

    @staticmethod
    def _format_sample(value):
        if value is None:
            return "空"
        text = str(value).replace("\r", " ").replace("\n", " ").strip()
        if not text:
            return "空"
        if len(text) > 20:
            text = text[:20] + "…"
        return text

    @staticmethod
    def _guess_column(names):
        for key in ("省", "省级名称", "名称", "NAME", "name", "Name"):
            if key in names:
                return key
        for name in names:
            if name:
                return name
        return ""

    def _pick_out(self):
        path = filedialog.askdirectory(title="选择输出目录")
        if path:
            self.out_var.set(path)

    def _open_out(self):
        out = self.out_var.get().strip().strip('"')
        if out and os.path.isdir(out):
            os.startfile(out)
        else:
            messagebox.showwarning("提示", "输出目录尚不存在。")

    def _open_preview(self):
        if self.preview_path and os.path.exists(self.preview_path):
            os.startfile(self.preview_path)
            return
        out = self.out_var.get().strip().strip('"')
        candidate = os.path.join(out, "preview.html") if out else ""
        if candidate and os.path.exists(candidate):
            self.preview_path = candidate
            os.startfile(candidate)
        else:
            messagebox.showwarning("提示", "还没有预览页，请先执行一次转换。")

    def _start(self):
        if self.worker and self.worker.is_alive():
            return
        path = self.shp_var.get().strip().strip('"')
        columns = self._selected_columns()
        out_dir = self.out_var.get().strip().strip('"')
        separator = self.sep_var.get()
        if not path:
            messagebox.showwarning("提示", "请先选择 Shapefile 文件。")
            return
        if not columns:
            messagebox.showwarning("提示", "请选择至少一个拆分列。")
            return
        if not out_dir:
            messagebox.showwarning("提示", "请选择输出目录。")
            return
        self.convert_btn.configure(state="disabled")
        self.progress.configure(value=0, maximum=100)
        self._log("开始转换 ...")
        self.worker = threading.Thread(
            target=self._run_convert,
            args=(path, columns, out_dir, self.prec_var.get(),
                  self.wgs_var.get(), self.repair_var.get(),
                  self.simplify_var.get(), self.csv_var.get(),
                  self.preview_var.get(), separator),
            daemon=True,
        )
        self.worker.start()

    def _run_convert(self, path, columns, out_dir, precision, wgs, repair, simplify,
                     export_csv, make_preview, separator):
        try:
            result = converter.convert(
                path, columns, out_dir,
                precision=precision, keep_crs=not wgs, repair=repair,
                simplify_meters=simplify, export_csv=export_csv,
                separator=separator,
                progress=lambda i, t: self.msg_queue.put(("progress", (i, t))),
                log=lambda m: self.msg_queue.put(("log", m)),
            )
            if make_preview:
                try:
                    result["preview_path"] = preview.build_preview(
                        out_dir, result.get("files"), "、".join(columns),
                        title="Shap2GeoJSON 预览",
                        log=lambda m: self.msg_queue.put(("log", m)),
                    )
                except Exception:
                    self.msg_queue.put(("log", "预览生成失败：\n" + traceback.format_exc()))
            self.msg_queue.put(("done", result))
        except Exception:
            self.msg_queue.put(("error", traceback.format_exc()))

    def _poll(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "progress":
                    i, t = payload
                    self.progress.configure(maximum=t, value=i)
                elif kind == "done":
                    self._finish(payload, None)
                elif kind == "error":
                    self._finish(None, payload)
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _finish(self, result, error):
        self.convert_btn.configure(state="normal")
        if error:
            self._log("发生错误：\n" + error)
            last = error.strip().splitlines()[-1] if error.strip() else "未知错误"
            messagebox.showerror("转换失败", last)
            return
        if result:
            self.progress.configure(maximum=result["total"], value=result["total"])
            self._log("完成：共 %d 条，生成 %d 个文件。" % (result["total"], result["written"]))
            extra = ""
            csv_path = result.get("csv_path")
            if csv_path:
                extra += "\n属性表：%s" % os.path.basename(csv_path)
            preview_path = result.get("preview_path")
            if preview_path:
                self.preview_path = preview_path
                extra += "\n预览页：%s" % os.path.basename(preview_path)
                try:
                    os.startfile(preview_path)
                except Exception:
                    pass
            messagebox.showinfo(
                "完成",
                "已生成 %d 个 GeoJSON 文件。%s\n输出目录：\n%s"
                % (result["written"], extra, result["out_dir"]),
            )

    def _log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


def main():
    if len(sys.argv) > 1:
        return _run_cli(sys.argv[1:])
    App().mainloop()
    return 0


def _run_cli(argv):
    import argparse

    parser = argparse.ArgumentParser(
        prog="Shap2GeoJSON",
        description="按属性列将 Shapefile 拆分为独立 GeoJSON 文件。",
    )
    parser.add_argument("shp", help="输入的 .shp 文件")
    parser.add_argument("column", help="用作拆分依据的属性列名，多列用逗号分隔，如 省级码,省")
    parser.add_argument("out_dir", help="输出目录")
    parser.add_argument("--precision", type=int, default=6, help="坐标小数位，默认 6")
    parser.add_argument("--simplify", type=float, default=100.0,
                        help="几何简化容差（米），0=不简化，默认 100")
    parser.add_argument("--sep", default="_", help="多列文件名的分隔符，默认 _")
    parser.add_argument("--keep-crs", action="store_true", help="保持原始坐标系（默认输出 WGS84）")
    parser.add_argument("--no-repair", action="store_true", help="不修复无效几何")
    parser.add_argument("--no-csv", action="store_true", help="不导出属性表 CSV")
    parser.add_argument("--no-preview", action="store_true", help="不生成 preview.html 预览页")
    parser.add_argument("--open-preview", action="store_true", help="生成预览页并用浏览器打开")
    args = parser.parse_args(argv)

    columns = [c for c in (part.strip() for part in args.column.split(",")) if c]
    result = converter.convert(
        args.shp, columns, args.out_dir,
        precision=args.precision, keep_crs=args.keep_crs,
        repair=not args.no_repair, simplify_meters=args.simplify,
        export_csv=not args.no_csv, separator=args.sep,
        log=lambda m: print(m),
    )
    print("完成：%d 条记录，生成 %d 个文件，输出到 %s"
          % (result["total"], result["written"], result["out_dir"]))

    if not args.no_preview:
        preview_path = preview.build_preview(
            args.out_dir, result.get("files"), "、".join(columns),
            title="Shap2GeoJSON 预览", log=lambda m: print(m))
        if args.open_preview:
            import webbrowser
            webbrowser.open("file:///" + preview_path.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

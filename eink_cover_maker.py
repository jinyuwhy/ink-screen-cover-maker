from __future__ import annotations

import io
import json
import difflib
import re
import threading
import traceback
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import BooleanVar, DoubleVar, StringVar, Tk, filedialog, messagebox
from tkinter import ttk

from PIL import Image, ImageEnhance, ImageFilter, ImageGrab, ImageOps, ImageTk


APP_TITLE = "墨水屏封面屏保"
DEFAULT_SCREEN_SIZE = (1264, 1680)
USER_AGENT = "InkScreenCoverMaker/1.0 (low-volume human-initiated desktop search)"
OUTPUT_DIR = Path(__file__).resolve().parent / "生成的屏保"
SEARCH_CACHE: dict[str, tuple[list["CoverCandidate"], list[str]]] = {}
SEARCH_CACHE_LOCK = threading.Lock()
DEVICE_PRESETS: dict[str, tuple[int, int] | None] = {
    "汉王 Clear 7 Turbo+（7英寸）": (1264, 1680),
    "手机型墨水屏（BOOX Palma 等）": (824, 1648),
    "6英寸 300PPI（Kindle/BOOX/Kobo）": (1072, 1448),
    "Kindle Paperwhite 11（6.8英寸）": (1236, 1648),
    "7英寸 300PPI（Kindle/BOOX/Kobo）": (1264, 1680),
    "7.8/10.3英寸常见尺寸": (1404, 1872),
    "8英寸 300PPI（Kobo Sage 等）": (1440, 1920),
    "10.2/10.3英寸 300PPI": (1860, 2480),
    "Supernote Manta（10.7英寸）": (1920, 2560),
    "13.3英寸 300PPI": (2400, 3200),
    "自定义": None,
}


@dataclass(frozen=True)
class CoverCandidate:
    title: str
    authors: str
    year: str
    source: str
    cover_url: str
    page_url: str = ""

    @property
    def label(self) -> str:
        details = " · ".join(part for part in (self.authors, self.year) if part)
        return f"{self.title} — {details or '作者/年份未知'}  ｜  {self.source}"


def request_bytes(url: str, timeout: int = 18) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def request_json(url: str) -> dict:
    return json.loads(request_bytes(url).decode("utf-8"))


def search_open_library(query: str) -> list[CoverCandidate]:
    params = urllib.parse.urlencode(
        {
            "q": query,
            "fields": "key,title,author_name,first_publish_year,cover_i",
            "limit": 20,
            "lang": "zh",
        }
    )
    data = request_json(f"https://openlibrary.org/search.json?{params}")
    results: list[CoverCandidate] = []
    for item in data.get("docs", []):
        cover_id = item.get("cover_i")
        if not cover_id:
            continue
        results.append(
            CoverCandidate(
                title=item.get("title") or query,
                authors="、".join((item.get("author_name") or [])[:3]),
                year=str(item.get("first_publish_year") or ""),
                source="Open Library",
                cover_url=f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg",
                page_url=(
                    f"https://openlibrary.org{item.get('key')}" if item.get("key") else ""
                ),
            )
        )
    return results


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in normalized if char.isalnum())


def title_relevance(query: str, title: str) -> float:
    query_key = normalize_title(query)
    title_key = normalize_title(title)
    if not query_key or not title_key:
        return 0.0
    if query_key == title_key:
        return 1.5
    ratio = difflib.SequenceMatcher(None, query_key, title_key).ratio()
    if query_key in title_key:
        return max(ratio, 1.25 - min((len(title_key) - len(query_key)) * 0.015, 0.25))
    if title_key in query_key and len(title_key) >= max(2, int(len(query_key) * 0.65)):
        return max(ratio, 1.05)
    return ratio


def search_all(query: str) -> tuple[list[CoverCandidate], list[str]]:
    cache_key = normalize_title(query)
    with SEARCH_CACHE_LOCK:
        cached = SEARCH_CACHE.get(cache_key)
    if cached is not None:
        return list(cached[0]), list(cached[1])

    errors: list[str] = []
    try:
        found = search_open_library(query)
    except Exception as exc:
        found = []
        errors.append(f"Open Library: {exc}")

    unique: list[CoverCandidate] = []
    seen: set[tuple[str, str, str]] = set()
    for item in found:
        key = (item.title.casefold(), item.authors.casefold(), item.cover_url)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    unique.sort(key=lambda item: title_relevance(query, item.title), reverse=True)
    relevant = [item for item in unique if title_relevance(query, item.title) >= 0.30]
    ranked = (relevant or unique[:8])[:35]
    with SEARCH_CACHE_LOCK:
        SEARCH_CACHE[cache_key] = (list(ranked), list(errors))
    return ranked, errors


def load_image_from_url(url: str) -> Image.Image:
    data = request_bytes(url)
    image = Image.open(io.BytesIO(data))
    image.load()
    return ImageOps.exif_transpose(image).convert("RGB")


def make_wallpaper(
    source: Image.Image,
    layout: str,
    gray_mode: str,
    contrast: float,
    sharpen: bool,
    target_size: tuple[int, int] = DEFAULT_SCREEN_SIZE,
) -> Image.Image:
    image = ImageOps.exif_transpose(source).convert("RGB")
    if layout.startswith("铺满"):
        composed = ImageOps.fit(image, target_size, method=Image.Resampling.LANCZOS)
    else:
        composed = Image.new("RGB", target_size, "white")
        fitted = ImageOps.contain(image, target_size, method=Image.Resampling.LANCZOS)
        x = (target_size[0] - fitted.width) // 2
        y = (target_size[1] - fitted.height) // 2
        composed.paste(fitted, (x, y))

    gray = ImageOps.grayscale(composed)
    gray = ImageOps.autocontrast(gray, cutoff=0.5)
    gray = ImageEnhance.Contrast(gray).enhance(contrast)
    if sharpen:
        gray = gray.filter(ImageFilter.UnsharpMask(radius=1.2, percent=125, threshold=3))

    if gray_mode.startswith("16"):
        gray = gray.point(lambda value: round(value / 17) * 17)
    elif gray_mode.startswith("黑白"):
        gray = gray.convert("1", dither=Image.Dither.FLOYDSTEINBERG).convert("L")
    return gray


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value[:80] or "书籍封面"


class CoverMakerApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1240x820")
        self.root.minsize(1040, 720)

        self.candidates: list[CoverCandidate] = []
        self.source_image: Image.Image | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.current_name = "书籍封面"

        self.query = StringVar()
        self.device_preset = StringVar(value=next(iter(DEVICE_PRESETS)))
        self.target_width = StringVar(value=str(DEFAULT_SCREEN_SIZE[0]))
        self.target_height = StringVar(value=str(DEFAULT_SCREEN_SIZE[1]))
        self.device_summary = StringVar()
        self.layout = StringVar(value="完整封面（推荐）")
        self.gray_mode = StringVar(value="256级灰度（推荐）")
        self.contrast = DoubleVar(value=1.15)
        self.sharpen = BooleanVar(value=True)
        self.status = StringVar(value="输入书名，搜索你喜欢的版本")

        self.update_device_summary()
        self._build_ui()

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Hint.TLabel", foreground="#666666")

        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="把最近读的书，变成墨水屏屏保", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            textvariable=self.device_summary,
            style="Hint.TLabel",
        ).pack(anchor="w", pady=(3, 14))

        search_bar = ttk.Frame(outer)
        search_bar.pack(fill="x", pady=(0, 14))
        entry = ttk.Entry(search_bar, textvariable=self.query, font=("Microsoft YaHei UI", 12))
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event: self.start_search())
        self.search_button = ttk.Button(search_bar, text="搜索封面", command=self.start_search)
        self.search_button.pack(side="left", padx=(8, 0))
        ttk.Button(search_bar, text="粘贴封面", command=self.paste_cover).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(search_bar, text="本地图片", command=self.choose_local_image).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(search_bar, text="网页查找", command=self.open_web_search).pack(
            side="left", padx=(8, 0)
        )

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)

        result_frame = ttk.Labelframe(body, text="搜索结果", padding=10)
        body.add(result_frame, weight=4)
        self.result_list = ttk.Treeview(
            result_frame, columns=(), show="tree", selectmode="browse", height=20
        )
        self.result_list.column("#0", width=380, minwidth=220, stretch=True)
        result_scroll = ttk.Scrollbar(result_frame, orient="vertical", command=self.result_list.yview)
        self.result_list.configure(yscrollcommand=result_scroll.set)
        self.result_list.pack(side="left", fill="both", expand=True)
        result_scroll.pack(side="right", fill="y")
        self.result_list.bind("<<TreeviewSelect>>", self.on_result_selected)
        self.result_list.bind("<Double-1>", self.open_selected_source)
        result_frame.bind(
            "<Configure>",
            lambda event: self.result_list.column("#0", width=max(220, event.width - 34)),
        )

        preview_frame = ttk.Labelframe(body, text="屏保预览", padding=10)
        body.add(preview_frame, weight=5)
        self.preview_label = ttk.Label(preview_frame, anchor="center", text="选择一本书后在这里预览")
        self.preview_label.pack(fill="both", expand=True)

        options = ttk.Labelframe(body, text="效果设置", padding=14)
        body.add(options, weight=3)

        ttk.Label(options, text="设备预设").pack(anchor="w")
        device_box = ttk.Combobox(
            options,
            textvariable=self.device_preset,
            values=tuple(DEVICE_PRESETS),
            state="readonly",
        )
        device_box.pack(fill="x", pady=(4, 8))
        device_box.bind("<<ComboboxSelected>>", self.on_device_selected)

        resolution_row = ttk.Frame(options)
        resolution_row.pack(fill="x", pady=(0, 14))
        width_entry = ttk.Entry(resolution_row, textvariable=self.target_width, width=7)
        width_entry.pack(side="left", fill="x", expand=True)
        ttk.Label(resolution_row, text=" × ").pack(side="left")
        height_entry = ttk.Entry(resolution_row, textvariable=self.target_height, width=7)
        height_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(resolution_row, text="交换", width=5, command=self.swap_resolution).pack(
            side="left", padx=(7, 0)
        )
        for entry_widget in (width_entry, height_entry):
            entry_widget.bind("<KeyRelease>", self.on_custom_resolution)
            entry_widget.bind("<Return>", lambda _event: self.refresh_preview())
            entry_widget.bind("<FocusOut>", lambda _event: self.refresh_preview())

        ttk.Label(options, text="排版").pack(anchor="w")
        layout_box = ttk.Combobox(
            options,
            textvariable=self.layout,
            values=("完整封面（推荐）", "铺满屏幕（会裁切）"),
            state="readonly",
        )
        layout_box.pack(fill="x", pady=(4, 14))
        layout_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh_preview())

        ttk.Label(options, text="墨水屏模式").pack(anchor="w")
        gray_box = ttk.Combobox(
            options,
            textvariable=self.gray_mode,
            values=("256级灰度（推荐）", "16级灰度", "黑白抖动"),
            state="readonly",
        )
        gray_box.pack(fill="x", pady=(4, 14))
        gray_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh_preview())

        ttk.Label(options, text="对比度").pack(anchor="w")
        contrast_scale = ttk.Scale(
            options,
            from_=0.75,
            to=1.8,
            variable=self.contrast,
            command=lambda _value: self.refresh_preview(),
        )
        contrast_scale.pack(fill="x", pady=(4, 10))
        ttk.Checkbutton(
            options, text="轻微锐化", variable=self.sharpen, command=self.refresh_preview
        ).pack(anchor="w")

        ttk.Separator(options).pack(fill="x", pady=18)
        ttk.Label(
            options,
            text="建议先用 256 级灰度。\n搜索不到时，可用“网页查找”后复制封面并粘贴。",
            style="Hint.TLabel",
            wraplength=210,
            justify="left",
        ).pack(anchor="w")
        ttk.Button(options, text="生成 PNG…", command=self.save_wallpaper).pack(
            fill="x", side="bottom", pady=(20, 0)
        )

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(12, 0))
        ttk.Label(bottom, textvariable=self.status, style="Hint.TLabel").pack(side="left")

        entry.focus_set()

    def read_target_size(self) -> tuple[int, int]:
        try:
            width = int(self.target_width.get().strip())
            height = int(self.target_height.get().strip())
        except ValueError as exc:
            raise ValueError("分辨率必须是整数") from exc
        if not (300 <= width <= 8000 and 300 <= height <= 8000):
            raise ValueError("宽度和高度需要在 300～8000 像素之间")
        if width * height > 40_000_000:
            raise ValueError("分辨率过大，请控制在 4000 万像素以内")
        return width, height

    def update_device_summary(self) -> None:
        try:
            width, height = self.read_target_size()
            orientation = "竖屏" if height >= width else "横屏"
            name = self.device_preset.get()
            self.device_summary.set(f"当前输出：{name} · {width} × {height} 像素 · {orientation} · 灰度 PNG")
        except ValueError:
            self.device_summary.set("当前输出：请填写有效的宽度和高度")

    def on_device_selected(self, _event=None) -> None:
        size = DEVICE_PRESETS.get(self.device_preset.get())
        if size is not None:
            self.target_width.set(str(size[0]))
            self.target_height.set(str(size[1]))
        self.update_device_summary()
        self.refresh_preview()

    def on_custom_resolution(self, _event=None) -> None:
        self.device_preset.set("自定义")
        self.update_device_summary()

    def swap_resolution(self) -> None:
        try:
            width, height = self.read_target_size()
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        self.target_width.set(str(height))
        self.target_height.set(str(width))
        self.device_preset.set("自定义")
        self.update_device_summary()
        self.refresh_preview()

    def set_busy(self, busy: bool, text: str) -> None:
        self.status.set(text)
        self.search_button.configure(state="disabled" if busy else "normal")
        self.root.configure(cursor="wait" if busy else "")

    def start_search(self) -> None:
        query = self.query.get().strip()
        if not query:
            messagebox.showinfo(APP_TITLE, "请先输入书名。")
            return
        self.set_busy(True, f"正在搜索《{query}》的封面…")
        threading.Thread(target=self._search_worker, args=(query,), daemon=True).start()

    def _search_worker(self, query: str) -> None:
        results, errors = search_all(query)
        self.root.after(0, lambda: self._show_search_results(query, results, errors))

    def _show_search_results(
        self, query: str, results: list[CoverCandidate], errors: list[str]
    ) -> None:
        self.set_busy(False, "")
        self.candidates = results
        self.result_list.delete(*self.result_list.get_children())
        for index, item in enumerate(results):
            self.result_list.insert("", "end", iid=str(index), text=item.label)

        if results:
            self.status.set(f"找到 {len(results)} 个相关封面；双击条目可打开来源网页")
            self.result_list.selection_set("0")
            self.result_list.focus("0")
            self.result_list.see("0")
            self.on_result_selected()
        else:
            detail = "\n".join(errors) if errors else "公开书库中没有找到封面"
            self.status.set("没有找到可用封面，可用网页查找后复制并粘贴")
            messagebox.showwarning(APP_TITLE, f"没有找到《{query}》的封面。\n\n{detail}")

    def on_result_selected(self, _event=None) -> None:
        selected = self.result_list.selection()
        if not selected:
            return
        index = int(selected[0])
        candidate = self.candidates[index]
        self.current_name = candidate.title
        self.set_busy(True, f"正在加载《{candidate.title}》封面…")
        threading.Thread(
            target=self._load_cover_worker, args=(candidate,), daemon=True
        ).start()

    def open_selected_source(self, _event=None) -> None:
        selected = self.result_list.selection()
        if not selected:
            return
        candidate = self.candidates[int(selected[0])]
        if candidate.page_url:
            webbrowser.open(candidate.page_url)

    def _load_cover_worker(self, candidate: CoverCandidate) -> None:
        try:
            image = load_image_from_url(candidate.cover_url)
            self.root.after(0, lambda: self._set_source_image(image, candidate.label))
        except Exception as exc:
            self.root.after(0, lambda: self._cover_error(str(exc)))

    def _cover_error(self, detail: str) -> None:
        self.set_busy(False, "封面加载失败，请尝试其他结果")
        messagebox.showerror(APP_TITLE, f"封面加载失败：\n{detail}")

    def _set_source_image(self, image: Image.Image, label: str) -> None:
        self.source_image = image
        self.set_busy(False, f"已选择 {label}")
        self.refresh_preview()

    def choose_local_image(self) -> None:
        path = filedialog.askopenfilename(
            title="选择封面图片",
            filetypes=(
                ("图片", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff"),
                ("所有文件", "*.*"),
            ),
        )
        if not path:
            return
        try:
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
            self.current_name = Path(path).stem
            self._set_source_image(image, Path(path).name)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"无法读取这张图片：\n{exc}")

    def open_web_search(self) -> None:
        query = self.query.get().strip()
        if not query:
            messagebox.showinfo(APP_TITLE, "请先输入书名。")
            return
        params = urllib.parse.urlencode({"search_text": query, "cat": "1001"})
        webbrowser.open(f"https://search.douban.com/book/subject_search?{params}")
        self.status.set("已在浏览器中打开搜索；复制封面图片后回到工具点击“粘贴封面”")

    def paste_cover(self) -> None:
        try:
            clipboard = ImageGrab.grabclipboard()
        except Exception:
            clipboard = None

        if isinstance(clipboard, Image.Image):
            self.current_name = self.query.get().strip() or "剪贴板封面"
            self._set_source_image(clipboard.convert("RGB"), "剪贴板图片")
            return

        if isinstance(clipboard, list):
            image_path = next((Path(path) for path in clipboard if Path(path).is_file()), None)
            if image_path is not None:
                try:
                    with Image.open(image_path) as opened:
                        image = ImageOps.exif_transpose(opened).convert("RGB")
                    self.current_name = image_path.stem
                    self._set_source_image(image, image_path.name)
                    return
                except Exception:
                    pass

        try:
            text = self.root.clipboard_get().strip()
        except Exception:
            text = ""
        if text.startswith(("https://", "http://")):
            self.current_name = self.query.get().strip() or "网络封面"
            self.set_busy(True, "正在读取剪贴板中的封面链接…")
            threading.Thread(
                target=self._load_pasted_url_worker,
                args=(text,),
                daemon=True,
            ).start()
            return

        messagebox.showinfo(
            APP_TITLE,
            "剪贴板里没有可用图片。\n\n请在网页或图片软件中复制图片；也可以复制图片的完整网址。",
        )

    def _load_pasted_url_worker(self, url: str) -> None:
        try:
            image = load_image_from_url(url)
            self.root.after(0, lambda: self._set_source_image(image, "剪贴板封面链接"))
        except Exception as exc:
            self.root.after(0, lambda: self._cover_error(str(exc)))

    def processed_image(self) -> Image.Image:
        if self.source_image is None:
            raise ValueError("还没有选择封面")
        target_size = self.read_target_size()
        return make_wallpaper(
            self.source_image,
            self.layout.get(),
            self.gray_mode.get(),
            self.contrast.get(),
            self.sharpen.get(),
            target_size,
        )

    def refresh_preview(self) -> None:
        if self.source_image is None:
            return
        try:
            preview = self.processed_image()
            preview.thumbnail((330, 470), Image.Resampling.LANCZOS)
            self.preview_photo = ImageTk.PhotoImage(preview)
            self.preview_label.configure(image=self.preview_photo, text="")
        except Exception as exc:
            self.status.set(f"预览失败：{exc}")

    def save_wallpaper(self) -> None:
        if self.source_image is None:
            messagebox.showinfo(APP_TITLE, "请先搜索并选择一个封面，或使用本地图片。")
            return
        try:
            width, height = self.read_target_size()
        except ValueError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        initial = f"{safe_filename(self.current_name)}_{width}x{height}_墨水屏.png"
        destination = filedialog.asksaveasfilename(
            title="保存屏保图片",
            initialdir=str(OUTPUT_DIR),
            initialfile=initial,
            defaultextension=".png",
            filetypes=(("PNG 图片", "*.png"),),
        )
        if not destination:
            return
        try:
            image = self.processed_image()
            image.save(destination, "PNG", optimize=True)
            self.status.set(f"已生成：{destination}")
            messagebox.showinfo(
                APP_TITLE,
                f"屏保已生成。\n\n尺寸：{image.width} × {image.height}\n模式：灰度 PNG\n位置：{destination}",
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"保存失败：\n{exc}")


def main() -> None:
    root = Tk()
    CoverMakerApp(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        error_log = Path(__file__).resolve().parent / "startup_error.log"
        error_log.write_text(traceback.format_exc(), encoding="utf-8")
        try:
            error_root = Tk()
            error_root.withdraw()
            messagebox.showerror(
                APP_TITLE,
                f"程序启动失败：\n{exc}\n\n详细信息已写入：\n{error_log}",
            )
            error_root.destroy()
        except Exception:
            pass

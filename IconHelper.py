#!/usr/bin/env python3

import os
import json
import subprocess
import threading
import time
import shutil
import datetime
import gi
gi.require_version('Gtk', '3.0')
import xml.etree.ElementTree as ET
import xml.sax
from typing import Callable, Dict, List, Optional, Tuple
from gi.repository import Gtk, GdkPixbuf, GLib, Gdk
import tempfile
from collections import OrderedDict
import hashlib
from pathlib import Path
import queue
import re

# --------------------------------------------------------------------------
# Globals and Constants
# --------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

PLACEHOLDER_PATH = os.path.join(SCRIPT_DIR, "emblem-unreadable.svg")
SYMLINK_EMBLEM_PATH = os.path.join(SCRIPT_DIR, "emblem-symlink.png")
BACKUP_EMBLEM_PATH = os.path.join(SCRIPT_DIR, "emblem-history.png")
TEMPLATE_SVG = os.path.join(SCRIPT_DIR, "template.svg")
BITMAP_SIZES = [16, 22, 24, 32, 48]
CATEGORIES_FILE = os.path.join(SCRIPT_DIR, "icon_categories.json")
PNG_EMBLEM = os.path.join(SCRIPT_DIR, "emblem-png.png")

# Config file
CONFIG_FILE = os.path.join(SCRIPT_DIR, "iconhelper_config.json")

# Supersampling defaults (we keep existing controls but will expose via Settings)
SUPERSAMPLE_ENABLED = True
SUPERSAMPLE_FACTOR = 3

# Disk cache defaults (exposed via Settings)
DEFAULT_DISK_CACHE_DIR = os.path.join(SCRIPT_DIR, ".thumbcache")
DISK_CACHE_ENABLED = True
DISK_CACHE_DIR = DEFAULT_DISK_CACHE_DIR
DISK_CACHE_SIZE_LIMIT = 200 * 1024 * 1024  # 200MB default

# Mint-Y export defaults (new settings)
MINTY_ENABLED = False
MINTY_EXPORT_PATH = ""  # when set, used as export root for Mint-Y style (can be absolute or inside theme)
MINTY_2X_ENABLED = True  # create @2x variants

# Loader pool defaults
PIXBUF_WORKER_COUNT = 6
ICON_PAGE_SIZE = 150  # number of icons to create initially / per page when scrolling

# Backups defaults
MAX_SVG_BACKUPS = 10  # per-icon backup limit

# In-memory pixbuf cache (LRU-like)
PIXBUF_CACHE: "OrderedDict[Tuple[str,int], GdkPixbuf.Pixbuf]" = OrderedDict()
CACHE_LOCK = threading.Lock()
MAX_PIXBUF_CACHE_ITEMS = 1200

# Disk cache index file
DISK_CACHE_INDEX = "index.json"
DISK_CACHE_LOCK = threading.Lock()

# Active preview popups registry
ACTIVE_PREVIEWS = set()
ACTIVE_PREVIEWS_LOCK = threading.Lock()

# Loader task queue; tasks are (path, size, callback)
_LOADER_QUEUE: "queue.Queue[Tuple[str,int,Callable]]" = queue.Queue()
_WORKERS_STARTED = False

# Mint-Y style rendering DPI factors (1x, optionally 2x for HiDPI)
MINTY_DPI_FACTORS = [1, 2]

# Detect inkscape DPI behaviour similar to moka script (90 vs 96)
try:
    ver_raw = subprocess.check_output(["inkscape", "-V"], stderr=subprocess.STDOUT).decode()
    m = re.search(r'(\d+)\.(\d+)', ver_raw)
    if m:
        major = int(m.group(1)); minor = int(m.group(2))
        if major == 0 and minor < 92:
            DPI_1_TO_1 = 90
        else:
            DPI_1_TO_1 = 96
    else:
        DPI_1_TO_1 = 96
except Exception:
    DPI_1_TO_1 = 96

# --------------------------------------------------------------------------
# Global backups (stored under SCRIPT_DIR, NOT inside themes)
# --------------------------------------------------------------------------

BACKUP_ROOT = os.path.join(SCRIPT_DIR, ".iconhelper_backups")
BACKUP_FILES_DIR = os.path.join(BACKUP_ROOT, "files")
BACKUP_INDEX_PATH = os.path.join(BACKUP_ROOT, "index.json")

def ensure_backup_dirs():
    try:
        os.makedirs(BACKUP_FILES_DIR, exist_ok=True)
    except Exception as e:
        print(f"Failed to create backup dirs: {e}")

def _load_backup_index() -> Dict[str, Dict]:
    try:
        if os.path.isfile(BACKUP_INDEX_PATH):
            with open(BACKUP_INDEX_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f"Failed to load backup index: {e}")
    return {}

def _save_backup_index(idx: Dict[str, Dict]):
    try:
        ensure_backup_dirs()
        with open(BACKUP_INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(idx, f, indent=2)
    except Exception as e:
        print(f"Failed to write backup index: {e}")

# --------------------------------------------------------------------------
# Config helpers
# --------------------------------------------------------------------------

def load_config():
    global SUPERSAMPLE_ENABLED, SUPERSAMPLE_FACTOR, DISK_CACHE_ENABLED, DISK_CACHE_DIR, DISK_CACHE_SIZE_LIMIT
    global PIXBUF_WORKER_COUNT, ICON_PAGE_SIZE, MAX_SVG_BACKUPS
    global MINTY_ENABLED, MINTY_EXPORT_PATH, MINTY_2X_ENABLED
    try:
        if os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            SUPERSAMPLE_ENABLED = bool(cfg.get("supersample_enabled", SUPERSAMPLE_ENABLED))
            SUPERSAMPLE_FACTOR = int(cfg.get("supersample_factor", SUPERSAMPLE_FACTOR))
            DISK_CACHE_ENABLED = bool(cfg.get("disk_cache_enabled", DISK_CACHE_ENABLED))
            DISK_CACHE_DIR = cfg.get("disk_cache_dir", DISK_CACHE_DIR)
            DISK_CACHE_SIZE_LIMIT = int(cfg.get("disk_cache_size_limit", DISK_CACHE_SIZE_LIMIT))
            PIXBUF_WORKER_COUNT = int(cfg.get("pixbuf_worker_count", PIXBUF_WORKER_COUNT))
            ICON_PAGE_SIZE = int(cfg.get("icon_page_size", ICON_PAGE_SIZE))
            MAX_SVG_BACKUPS = int(cfg.get("max_svg_backups", MAX_SVG_BACKUPS))
            MINTY_ENABLED = bool(cfg.get("minty_enabled", MINTY_ENABLED))
            MINTY_EXPORT_PATH = cfg.get("minty_export_path", MINTY_EXPORT_PATH)
            MINTY_2X_ENABLED = bool(cfg.get("minty_2x_enabled", MINTY_2X_ENABLED))
    except Exception as e:
        print(f"Failed to load config {CONFIG_FILE}: {e}")

def save_config():
    try:
        cfg = {
            "supersample_enabled": SUPERSAMPLE_ENABLED,
            "supersample_factor": SUPERSAMPLE_FACTOR,
            "disk_cache_enabled": DISK_CACHE_ENABLED,
            "disk_cache_dir": DISK_CACHE_DIR,
            "disk_cache_size_limit": DISK_CACHE_SIZE_LIMIT,
            "pixbuf_worker_count": PIXBUF_WORKER_COUNT,
            "icon_page_size": ICON_PAGE_SIZE,
            "max_svg_backups": MAX_SVG_BACKUPS,
            "minty_enabled": MINTY_ENABLED,
            "minty_export_path": MINTY_EXPORT_PATH,
            "minty_2x_enabled": MINTY_2X_ENABLED
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"Failed to save config {CONFIG_FILE}: {e}")

# Load config at startup (overrides defaults)
load_config()

# --------------------------------------------------------------------------
# Disk cache utilities (LRU eviction by last_used)
# --------------------------------------------------------------------------

def ensure_disk_cache_dir():
    global DISK_CACHE_DIR
    if not DISK_CACHE_DIR:
        DISK_CACHE_DIR = DEFAULT_DISK_CACHE_DIR
    try:
        os.makedirs(DISK_CACHE_DIR, exist_ok=True)
    except Exception as e:
        print(f"Failed to create disk cache dir {DISK_CACHE_DIR}: {e}")

def _disk_index_path():
    return os.path.join(DISK_CACHE_DIR, DISK_CACHE_INDEX)

def _load_disk_index() -> Dict[str, Dict]:
    """
    Load the disk cache index and return a dict. If the file contains a
    legacy/list format (or is corrupt), try to heal common cases or return {}.
    """
    try:
        idx_path = _disk_index_path()
        if os.path.isfile(idx_path):
            with open(idx_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                return data

            if isinstance(data, list):
                healed = {}
                for item in data:
                    if isinstance(item, dict):
                        key = item.get("key")
                        if not key:
                            fname = item.get("fname") or item.get("file") or ""
                            if fname:
                                key = hashlib.sha1(fname.encode("utf-8")).hexdigest()
                        if key:
                            healed[key] = item
                return healed
    except Exception:
        pass
    return {}

def _save_disk_index(idx: Dict[str, Dict]):
    try:
        ensure_disk_cache_dir()
        with open(_disk_index_path(), "w", encoding="utf-8") as f:
            json.dump(idx, f, indent=2)
    except Exception as e:
        print(f"Failed to write disk cache index: {e}")

def _cache_key_for(path: str, size: int) -> str:
    try:
        mtime = int(os.path.getmtime(path))
    except Exception:
        mtime = 0
    key = f"{os.path.abspath(path)}|{mtime}|{size}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()

def get_disk_cache_path(path: str, size: int) -> Optional[str]:
    if not DISK_CACHE_ENABLED:
        return None
    try:
        ensure_disk_cache_dir()
        fname = _cache_key_for(path, size) + ".png"
        return os.path.join(DISK_CACHE_DIR, fname)
    except Exception:
        return None

def _get_disk_cache_total_size_and_count(idx: Dict[str, Dict]) -> Tuple[int,int]:
    total = 0
    cnt = 0
    for v in idx.values():
        if isinstance(v, dict):
            sz = v.get("size", 0)
            total += sz
            cnt += 1
    return total, cnt

def _prune_disk_cache_if_needed():
    if not DISK_CACHE_ENABLED:
        return
    try:
        with DISK_CACHE_LOCK:
            idx = _load_disk_index()
            if not isinstance(idx, dict):
                return
            total, cnt = _get_disk_cache_total_size_and_count(idx)
            if total <= DISK_CACHE_SIZE_LIMIT:
                return
            # Evict by oldest last_used (entries missing last_used treated as oldest)
            items = sorted(idx.items(), key=lambda kv: kv[1].get("last_used", 0) if isinstance(kv[1], dict) else 0)
            for key, meta in items:
                if total <= DISK_CACHE_SIZE_LIMIT:
                    break
                fname = meta.get("fname") if isinstance(meta, dict) else None
                p = os.path.join(DISK_CACHE_DIR, fname) if fname else None
                try:
                    size_removed = meta.get("size", 0) if isinstance(meta, dict) else 0
                    if p and os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                        if not size_removed:
                            try:
                                size_removed = os.path.getsize(p)
                            except Exception:
                                size_removed = 0
                        total -= size_removed
                except Exception:
                    pass
                idx.pop(key, None)
            _save_disk_index(idx)
    except Exception as e:
        print(f"Disk prune error: {e}")

# store disk cache and update index (atomic, robust)
def store_disk_cache(path: str, size: int, pixbuf: GdkPixbuf.Pixbuf):
    if not DISK_CACHE_ENABLED:
        return
    try:
        ensure_disk_cache_dir()
        cache_path = get_disk_cache_path(path, size)
        if not cache_path:
            return
        # create a tmp file inside the cache dir to avoid cross-filesystem/permission races
        tmp = None
        try:
            # create named temp file in disk cache dir
            with tempfile.NamedTemporaryFile(dir=DISK_CACHE_DIR, delete=False, suffix=".png.tmp") as tf:
                tmp = tf.name
            # Try saving via GdkPixbuf first
            saved = False
            try:
                pixbuf.savev(tmp, "png", [], [])
                saved = True
            except Exception:
                # fallback to PIL if available
                try:
                    from PIL import Image
                    buf = pixbuf.get_pixels()
                    width = pixbuf.get_width()
                    height = pixbuf.get_height()
                    rowstride = pixbuf.get_rowstride()
                    has_alpha = pixbuf.get_has_alpha()
                    mode = "RGBA" if has_alpha else "RGB"
                    img = Image.frombytes(mode, (width, height), buf, "raw", mode, rowstride)
                    img.save(tmp, format="PNG")
                    saved = True
                except Exception:
                    # last-ditch: try pixbuf.savev again (some pixbuf implementations behave differently)
                    try:
                        pixbuf.savev(tmp, "png", [], [])
                        saved = True
                    except Exception:
                        saved = False
            if saved and os.path.exists(tmp):
                try:
                    os.replace(tmp, cache_path)
                except FileNotFoundError:
                    # tmp got removed concurrently; ignore
                    pass
                except Exception as e:
                    print(f"Failed to move temp cache file into place: {e}")
                try:
                    with DISK_CACHE_LOCK:
                        idx = _load_disk_index()
                        key = _cache_key_for(path, size)
                        try:
                            stat = os.stat(cache_path)
                            idx[key] = {"fname": os.path.basename(cache_path), "size": stat.st_size, "last_used": int(time.time())}
                            _save_disk_index(idx)
                        except Exception:
                            # if stat fails, still try to record something
                            idx[key] = {"fname": os.path.basename(cache_path), "size": 0, "last_used": int(time.time())}
                            _save_disk_index(idx)
                    threading.Thread(target=_prune_disk_cache_if_needed, daemon=True).start()
                except Exception as e:
                    print(f"Failed to update disk cache index: {e}")
        finally:
            # cleanup tmp if still present
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
    except Exception as e:
        print(f"Failed to store disk cache for {path} size {size}: {e}")

def load_disk_cache(path: str, size: int) -> Optional[GdkPixbuf.Pixbuf]:
    if not DISK_CACHE_ENABLED:
        return None
    try:
        cache_path = get_disk_cache_path(path, size)
        if cache_path and os.path.isfile(cache_path):
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file(cache_path)
                with DISK_CACHE_LOCK:
                    idx = _load_disk_index()
                    key = _cache_key_for(path, size)
                    if key in idx and isinstance(idx[key], dict):
                        idx[key]["last_used"] = int(time.time())
                        _save_disk_index(idx)
                return pb
            except Exception:
                try:
                    return GdkPixbuf.Pixbuf.new_from_file_at_size(cache_path, size, size)
                except Exception:
                    return None
    except Exception:
        pass
    return None

def invalidate_disk_cache_for_path(path: str):
    if not DISK_CACHE_ENABLED:
        return
    try:
        with DISK_CACHE_LOCK:
            idx = _load_disk_index()
            if not isinstance(idx, dict):
                return
            for size in BITMAP_SIZES + [64, 96, 256, 512]:
                key = _cache_key_for(path, size)
                meta = idx.pop(key, None)
                if meta and isinstance(meta, dict):
                    p = os.path.join(DISK_CACHE_DIR, meta.get("fname", ""))
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass
            _save_disk_index(idx)
    except Exception as e:
        print(f"Failed to invalidate disk cache for {path}: {e}")

# --------------------------------------------------------------------------
# In-memory pixbuf cache helpers
# --------------------------------------------------------------------------

def _cache_get(key):
    with CACHE_LOCK:
        val = PIXBUF_CACHE.get(key)
        if val is not None:
            PIXBUF_CACHE.move_to_end(key)
        return val

def _cache_set(key, pixbuf):
    with CACHE_LOCK:
        PIXBUF_CACHE[key] = pixbuf
        PIXBUF_CACHE.move_to_end(key)
        while len(PIXBUF_CACHE) > MAX_PIXBUF_CACHE_ITEMS:
            PIXBUF_CACHE.popitem(last=False)

def clear_pixbuf_cache():
    with CACHE_LOCK:
        PIXBUF_CACHE.clear()

def invalidate_pixbuf_cache_for_path(path):
    with CACHE_LOCK:
        keys_to_remove = [k for k in PIXBUF_CACHE.keys() if k[0] == path]
        for k in keys_to_remove:
            PIXBUF_CACHE.pop(k, None)
    try:
        invalidate_disk_cache_for_path(path)
    except Exception:
        pass

# --------------------------------------------------------------------------
# Loader pool (bounded worker threads)
# --------------------------------------------------------------------------

def _start_loader_workers():
    global _WORKERS_STARTED
    if _WORKERS_STARTED:
        return
    _WORKERS_STARTED = True
    for i in range(max(1, PIXBUF_WORKER_COUNT)):
        t = threading.Thread(target=_loader_worker, daemon=True, name=f"pixbuf-worker-{i}")
        t.start()

def _loader_worker():
    while True:
        try:
            path, size, cb = _LOADER_QUEUE.get()
            key = (path, size)
            pix = _cache_get(key)
            if pix:
                GLib.idle_add(cb, pix)
                _LOADER_QUEUE.task_done()
                continue
            pix = load_disk_cache(path, size)
            if pix:
                _cache_set(key, pix)
                GLib.idle_add(cb, pix)
                _LOADER_QUEUE.task_done()
                continue
            try:
                if not path or not os.path.exists(path):
                    source = PLACEHOLDER_PATH
                else:
                    source = path
                pix = GdkPixbuf.Pixbuf.new_from_file_at_size(source, size, size)
            except Exception:
                try:
                    pix = GdkPixbuf.Pixbuf.new_from_file_at_size(PLACEHOLDER_PATH, size, size)
                except Exception:
                    pix = None
            if pix:
                _cache_set(key, pix)
                try:
                    store_disk_cache(path if os.path.exists(path) else PLACEHOLDER_PATH, size, pix)
                except Exception:
                    pass
                GLib.idle_add(cb, pix)
            _LOADER_QUEUE.task_done()
        except Exception:
            try:
                _LOADER_QUEUE.task_done()
            except Exception:
                pass
            time.sleep(0.1)

def enqueue_pixbuf_load(path: str, size: int, callback: Callable[[GdkPixbuf.Pixbuf], None]):
    _start_loader_workers()
    key = (path if path else PLACEHOLDER_PATH, size)
    pix = _cache_get(key)
    if pix:
        GLib.idle_add(callback, pix)
        return
    disk = load_disk_cache(key[0], size)
    if disk:
        _cache_set(key, disk)
        GLib.idle_add(callback, disk)
        return
    try:
        _LOADER_QUEUE.put((key[0], size, callback))
    except Exception:
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(key[0], size, size)
            _cache_set(key, pb)
            GLib.idle_add(callback, pb)
        except Exception:
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_size(PLACEHOLDER_PATH, size, size)
                GLib.idle_add(callback, pb)
            except Exception:
                pass

# --------------------------------------------------------------------------
# Helper to close previews
# --------------------------------------------------------------------------

def close_all_previews():
    try:
        with ACTIVE_PREVIEWS_LOCK:
            for p in list(ACTIVE_PREVIEWS):
                try:
                    p.destroy()
                except Exception:
                    pass
            ACTIVE_PREVIEWS.clear()
    except Exception:
        pass

# --------------------------------------------------------------------------
# Utility Functions
# --------------------------------------------------------------------------

def check_file_exists(path: str) -> bool:
    if not os.path.isfile(path):
        print(f"Required file missing: {path}")
        return False
    return True

# --------------------------------------------------------------------------
# LazyIconBox Widget
# --------------------------------------------------------------------------

class LazyIconBox(Gtk.EventBox):
    def __init__(self, icon_name: str, icon_path: str, click_cb: Callable):
        super().__init__()
        self.icon_name = icon_name
        self.icon_path = icon_path
        self.click_cb = click_cb

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add(vbox)

        self.overlay = Gtk.Overlay()
        vbox.pack_start(self.overlay, False, False, 0)

        # Container for multiple small emblems in the top-right corner
        self.top_right_emblems = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.top_right_emblems.set_halign(Gtk.Align.END)
        self.top_right_emblems.set_valign(Gtk.Align.START)
        # Make it non-visible-window (so only images show)
        self.top_right_emblems.set_no_show_all(False)
        self.overlay.add_overlay(self.top_right_emblems)
        self.top_right_emblems.show()

        self.image = Gtk.Image()
        self.overlay.add(self.image)

        label = Gtk.Label(label=icon_name)
        label.set_ellipsize(True)
        label.set_max_width_chars(15)
        vbox.pack_start(label, False, False, 0)

        self.connect("button-press-event", self.on_button_press)

        placeholder_pix = get_or_load_pixbuf_sync(PLACEHOLDER_PATH, 64)
        if placeholder_pix:
            self.image.set_from_pixbuf(placeholder_pix)

        self.update_icon(icon_path)

        self.hover_timeout_id = None
        self.popup = None
        self._enlarge_image_widget = None
        self.connect("enter-notify-event", self.on_mouse_enter)
        self.connect("leave-notify-event", self.on_mouse_leave)

    def on_mouse_enter(self, widget, event):
        if self.hover_timeout_id is None:
            self.hover_timeout_id = GLib.timeout_add(700, self.show_enlarged_preview)
        return True

    def on_mouse_leave(self, widget, event):
        if self.hover_timeout_id is not None:
            try:
                GLib.source_remove(self.hover_timeout_id)
            except Exception:
                pass
            self.hover_timeout_id = None
        self.hide_enlarged_preview()
        return True

    def cancel_hover(self):
        if getattr(self, "hover_timeout_id", None) is not None:
            try:
                GLib.source_remove(self.hover_timeout_id)
            except Exception:
                pass
            self.hover_timeout_id = None

    def show_enlarged_preview(self):
        if self.popup:
            try:
                self.popup.destroy()
            except Exception:
                pass
            self.popup = None

        self.popup = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.popup.set_decorated(False)
        self.popup.set_border_width(8)
        self.popup.set_resizable(False)

        large_placeholder = get_or_load_pixbuf_sync(PLACEHOLDER_PATH, 512)
        image = Gtk.Image.new_from_pixbuf(large_placeholder)
        self._enlarge_image_widget = image

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.pack_start(image, True, True, 0)
        label = Gtk.Label(label=self.icon_name)
        box.pack_start(label, False, False, 0)
        self.popup.add(box)
        self.popup.show_all()

        try:
            with ACTIVE_PREVIEWS_LOCK:
                ACTIVE_PREVIEWS.add(self.popup)
        except Exception:
            pass

        try:
            display = Gdk.Display.get_default()
            pointer = display.get_default_seat().get_pointer()
            screen, x, y = pointer.get_position()
            self.popup.move(x + 16, y + 16)
        except Exception:
            pass

        def _set_large(pb):
            if self.popup and self._enlarge_image_widget:
                try:
                    self._enlarge_image_widget.set_from_pixbuf(pb)
                except Exception:
                    pass

        enqueue_pixbuf_load(self.icon_path, 512, _set_large)

        self.hover_timeout_id = None
        return False

    def hide_enlarged_preview(self):
        try:
            with ACTIVE_PREVIEWS_LOCK:
                if self.popup in ACTIVE_PREVIEWS:
                    ACTIVE_PREVIEWS.discard(self.popup)
        except Exception:
            pass
        if self.popup:
            try:
                self.popup.destroy()
            except Exception:
                pass
            self.popup = None
        if getattr(self, "hover_timeout_id", None) is not None:
            try:
                GLib.source_remove(self.hover_timeout_id)
            except Exception:
                pass
            self.hover_timeout_id = None

    def update_icon(self, icon_path: str):
        requested_path = icon_path if icon_path else PLACEHOLDER_PATH
        placeholder_pix = get_or_load_pixbuf_sync(PLACEHOLDER_PATH, 64)
        if placeholder_pix:
            self.image.set_from_pixbuf(placeholder_pix)

        # remove any individual emblem attributes
        for attr in ('emblem', 'png_emblem', 'versions_emblem', 'warning_overlay'):
            if hasattr(self, attr):
                try:
                    w = getattr(self, attr)
                    # if the widget was packed into the top_right_emblems container, unparent it
                    if getattr(self, 'top_right_emblems', None) and w.get_parent() is self.top_right_emblems:
                        try:
                            self.top_right_emblems.remove(w)
                        except Exception:
                            pass
                    else:
                        try:
                            self.overlay.remove(w)
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    delattr(self, attr)
                except Exception:
                    pass

        # also clear any remaining widgets inside the top_right_emblems container
        try:
            if getattr(self, 'top_right_emblems', None):
                for child in list(self.top_right_emblems.get_children()):
                    try:
                        self.top_right_emblems.remove(child)
                    except Exception:
                        pass
        except Exception:
            pass

        def _on_pix_loaded(pb):
            try:
                self.image.set_from_pixbuf(pb)
                if requested_path and os.path.exists(requested_path):
                    self.icon_path = requested_path
                else:
                    self.icon_path = PLACEHOLDER_PATH
            except Exception:
                pass

        enqueue_pixbuf_load(requested_path, 64, _on_pix_loaded)

        try:
            if requested_path != PLACEHOLDER_PATH and os.path.islink(requested_path):
                if check_file_exists(SYMLINK_EMBLEM_PATH):
                    emblem_pixbuf = get_or_load_pixbuf_sync(SYMLINK_EMBLEM_PATH, 16)
                    if emblem_pixbuf:
                        self.emblem = Gtk.Image.new_from_pixbuf(emblem_pixbuf)
                        self.emblem.set_halign(Gtk.Align.END)
                        self.emblem.set_valign(Gtk.Align.START)
                        self.top_right_emblems.pack_start(self.emblem, False, False, 0)
                        self.emblem.show()
        except Exception:
            pass

        try:
            if requested_path != PLACEHOLDER_PATH and requested_path.lower().endswith('.png'):
                if check_file_exists(PNG_EMBLEM):
                    png_emblem_pixbuf = get_or_load_pixbuf_sync(PNG_EMBLEM, 16)
                    if png_emblem_pixbuf:
                        self.png_emblem = Gtk.Image.new_from_pixbuf(png_emblem_pixbuf)
                        self.png_emblem.set_halign(Gtk.Align.END)
                        self.png_emblem.set_valign(Gtk.Align.START)
                        self.top_right_emblems.pack_start(self.png_emblem, False, False, 0)
                        self.png_emblem.show()
        except Exception:
            pass

        try:
            if requested_path.lower().endswith(".svg") and os.path.exists(requested_path):
                size_bytes = os.path.getsize(requested_path)
                if size_bytes > 1024 * 1024:
                    warning_pixbuf = get_or_load_pixbuf_sync(os.path.join(SCRIPT_DIR, "warning-triangle.svg"), 20)
                    if warning_pixbuf:
                        warn_eventbox = Gtk.EventBox()
                        warning_img = Gtk.Image.new_from_pixbuf(warning_pixbuf)
                        warn_eventbox.add(warning_img)
                        warn_eventbox.set_tooltip_text("SVG file too large: %.1f MB" % (size_bytes / (1024 * 1024)))
                        warn_eventbox.set_visible_window(False)
                        warn_eventbox.set_halign(Gtk.Align.START)
                        warn_eventbox.set_valign(Gtk.Align.START)
                        self.warning_overlay = warn_eventbox
                        self.overlay.add_overlay(self.warning_overlay)
                        self.warning_overlay.show_all()
        except Exception:
            pass

        # Versions emblem: show if backups exist (requires icon_helper to be set)
        try:
            helper = getattr(self, "icon_helper", None)
            if helper:
                # Try to determine category more reliably:
                category = helper.current_category
                # If icon_path is available, try to infer category from it relative to theme_path
                try:
                    theme_root = helper.theme_path
                    if not category and theme_root and self.icon_path:
                        try:
                            rel = os.path.relpath(self.icon_path, theme_root)
                            parts = rel.split(os.sep)
                            # Expect structure like "<category>/<size>/<file>"
                            if len(parts) >= 2:
                                category = parts[0]
                        except Exception:
                            category = category
                except Exception:
                    pass

                # Final fallback: scan icon_categories to find the icon name
                if not category:
                    for cat, icons in helper.icon_categories.items():
                        if self.icon_name in icons:
                            category = cat
                            break

                if category:
                    backups = helper.list_backups(self.icon_name, category)
                    if backups:
                        # Add emblem overlay same way as PNG_EMBLEM / SYMLINK_EMBLEM
                        if check_file_exists(BACKUP_EMBLEM_PATH):
                            try:
                                versions_pix = get_or_load_pixbuf_sync(BACKUP_EMBLEM_PATH, 16)
                                if versions_pix:
                                    self.versions_emblem = Gtk.Image.new_from_pixbuf(versions_pix)
                                    # match position/style of PNG/SYMLINK emblems (END / START)
                                    self.versions_emblem.set_halign(Gtk.Align.END)
                                    self.versions_emblem.set_valign(Gtk.Align.START)
                                    self.overlay.add_overlay(self.versions_emblem)
                                    self.versions_emblem.show()
                            except Exception:
                                pass
        except Exception:
            pass

    def on_button_press(self, widget, event):
        if event.button == 3:
            self.show_context_menu(event)
            return True
        elif event.button == 1:
            self.click_cb(self.icon_path, self.icon_name)
            return True
        return False

    def delete_icon(self, menu_item):
        dialog = Gtk.MessageDialog(
            transient_for=self.get_toplevel(),
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Delete icon '{self.icon_name}' in all sizes?",
        )
        dialog.format_secondary_text(
            "This will delete all files (PNG, SVG, symlinks) with this name for all sizes in this category."
        )
        remove_check = Gtk.CheckButton(label="Permanently remove icon from theme")
        remove_check.set_tooltip_text("Also remove this icon from the icon list (JSON) so it never appears again.")
        dialog.get_content_area().pack_start(remove_check, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        remove_from_json = remove_check.get_active()
        dialog.destroy()
        if response == Gtk.ResponseType.YES and hasattr(self, 'icon_helper'):
            self.icon_helper.delete_icon_files(self.icon_name, remove_from_json=remove_from_json)

    def show_context_menu(self, event):
        menu = Gtk.Menu()
        is_missing = (self.icon_path == PLACEHOLDER_PATH)
        is_svg = (not is_missing and self.icon_path.lower().endswith(".svg") and not os.path.islink(self.icon_path))
        if is_svg:
            edit_item = Gtk.MenuItem(label="Edit Metadata")
            edit_item.connect("activate", self.edit_metadata)
            menu.append(edit_item)
            # Versions submenu if helper has backups
            try:
                helper = getattr(self, "icon_helper", None)
                if helper:
                    category = helper.current_category
                    if category is None:
                        for cat, icons in helper.icon_categories.items():
                            if self.icon_name in icons:
                                category = cat
                                break
                    if category:
                        if helper.list_backups(self.icon_name, category):
                            versions_item = Gtk.MenuItem(label="Versions...")
                            versions_item.connect("activate", lambda w: helper.show_versions_dialog(self.icon_name, category))
                            menu.append(versions_item)
            except Exception:
                pass
        if not is_missing:
            clear_item = Gtk.MenuItem(label="Clear Existing Icon")
            clear_item.connect("activate", self.clear_icon)
            menu.append(clear_item)
        remove_item = Gtk.MenuItem(label="Permanently remove icon from theme")
        remove_item.connect("activate", self.permanently_remove_icon)
        menu.append(remove_item)
        menu.show_all()
        menu.popup(None, None, None, None, event.button, event.time)

    def show_metadata_menu(self, event):
        menu = Gtk.Menu()
        edit_item = Gtk.MenuItem(label="Edit Metadata")
        edit_item.connect("activate", self.edit_metadata)
        menu.append(edit_item)
        menu.show_all()
        menu.popup(None, None, None, None, event.button, event.time)

    def edit_metadata(self, menu_item):
        if hasattr(self, 'icon_helper'):
            self.icon_helper.show_svg_metadata_dialog(self.icon_path)

    def clear_icon(self, menu_item):
        dialog = Gtk.MessageDialog(
            transient_for=self.get_toplevel(),
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Clear '{self.icon_name}'?",
        )
        dialog.format_secondary_text(
            "This will clear the existing bitmaps and svg files from the theme and leave a empty icon."
        )
        dialog.show_all()
        response = dialog.run()
        dialog.destroy()
        if hasattr(self, 'icon_helper'):
            self.icon_helper.delete_icon_files(self.icon_name, remove_from_json=False)

    def permanently_remove_icon(self, menu_item):
        dialog = Gtk.MessageDialog(
            transient_for=self.get_toplevel(),
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Permanently remove '{self.icon_name}' from the theme?",
        )
        dialog.format_secondary_text(
            "This will remove the icon from the icon list and it will not show up (even as missing) in this category."
        )
        dialog.show_all()
        response = dialog.run()
        dialog.destroy()
        if hasattr(self, 'icon_helper'):
            self.icon_helper.delete_icon_files(self.icon_name, remove_from_json=True)

# --------------------------------------------------------------------------
# IconThemeHelper Main Window
# --------------------------------------------------------------------------

class IconThemeHelper(Gtk.Window):
    def __init__(self):
        super().__init__(title="Icon Theme Helper")
        self.set_default_size(1200, 800)

        self.icon_categories: Dict[str, List[str]] = {}
        self.theme_path: Optional[str] = None
        self.current_category: Optional[str] = None
        self.icon_index: Dict[str, str] = {}
        self.icon_boxes: List[LazyIconBox] = []
        self.indexing_done: bool = False
        self._export_progress_dialog = None
        self._export_progress_bar = None
        self._export_progress_label = None
        self._export_total_tasks = 0
        self._export_done_tasks = 0
        self._export_cancel_requested = False
        self._export_lock = threading.Lock()

        icon_path = os.path.join(SCRIPT_DIR, 'icon-helper-logo.svg')
        if os.path.exists(icon_path):
            self.set_icon_from_file(icon_path)

        self.search_text: str = ""

        if not check_file_exists(CATEGORIES_FILE):
            self.show_message("Error", f"Missing categories file: {CATEGORIES_FILE}")
            return

        try:
            with open(CATEGORIES_FILE, "r", encoding="utf-8") as f:
                self.icon_categories = json.load(f)
        except Exception as e:
            self.show_message("Error", f"Cannot load categories: {e}")
            return

        self._page_loaded_until = 0
        self._current_filtered_list: List[str] = []
        self.current_status_filter = "All Icons"


        self.setup_ui()

    def setup_ui(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add(main_box)

        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        # Top controls: Refresh & Choose Theme
        top_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        refresh_btn = Gtk.Button(label="Refresh Icons")
        refresh_btn.connect("clicked", self.on_refresh_clicked)
        choose_btn = Gtk.Button(label="Choose Theme Folder")
        choose_btn.connect("clicked", self.on_choose_theme)
        top_btn_box.pack_start(refresh_btn, True, True, 0)
        top_btn_box.pack_start(choose_btn, True, True, 0)
        sidebar_box.pack_start(top_btn_box, False, False, 0)

        # Category list (keeps most prominent position)
        self.category_list = Gtk.ListBox()
        self.category_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.category_list.connect("row-selected", self.on_category_selected)
        self.category_list.set_sensitive(False)

        for category in self.icon_categories.keys():
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=category, xalign=0)
            row.add(label)
            self.category_list.add(row)

        sidebar_box.pack_start(self.category_list, True, True, 0)

        # Search -- placed directly under category list
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        search_label = Gtk.Label(label="Search:", xalign=0)
        self.search_entry = Gtk.Entry()
        self.search_entry.set_placeholder_text("Filter icons by name...")
        self.search_entry.connect("changed", self.on_search_changed)
        search_box.pack_start(search_label, False, False, 0)
        search_box.pack_start(self.search_entry, True, True, 0)
        sidebar_box.pack_start(search_box, False, False, 0)

        # Export mode indicator -- under Search
        self.export_mode_label = Gtk.Label(label=self._export_mode_text(), xalign=0)
        sidebar_box.pack_start(self.export_mode_label, False, False, 0)

        # Create Symlink button -- under export mode
        self.create_symlink_btn = Gtk.Button(label="Create Symlink")
        self.create_symlink_btn.set_sensitive(False)
        self.create_symlink_btn.connect("clicked", self.on_create_symlink_clicked)
        sidebar_box.pack_start(self.create_symlink_btn, False, False, 0)

        # Icon grid filter dropdown -- under Create Symlink
        self.status_filter_combo = Gtk.ComboBoxText()
        filter_items = ["All Icons", "All Except Symlinks", "Missing Icons", "Only SVG", "Only PNG", "Symlinks Only", "Large Files"]
        for t in filter_items:
            self.status_filter_combo.append_text(t)
        # set active to current_status_filter if present
        try:
            idx = filter_items.index(getattr(self, "current_status_filter", "All Icons"))
        except Exception:
            idx = 0
        self.status_filter_combo.set_active(idx)
        self.status_filter_combo.connect("changed", self.on_status_filter_changed)
        sidebar_box.pack_start(self.status_filter_combo, False, False, 0)

        # Settings button -- under the filter dropdown
        settings_btn = Gtk.Button(label="Settings...")
        settings_btn.connect("clicked", self.on_settings_clicked)
        self.settings_btn = settings_btn
        sidebar_box.pack_start(settings_btn, False, False, 0)

        main_box.pack_start(sidebar_box, False, False, 0)

        # Icon grid area
        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.set_max_children_per_line(10)
        self.flowbox.set_selection_mode(Gtk.SelectionMode.NONE)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.add(self.flowbox)
        main_box.pack_start(self.scrolled, True, True, 0)

        # connect scroll adjustment for incremental loading
        vadj = self.scrolled.get_vadjustment()
        vadj.connect("value-changed", self.on_scroll_adjustment)

    def _export_mode_text(self):
        if MINTY_ENABLED:
            return "Export mode: Mint-Y style"
        else:
            return "Export mode: Mint-X Style"

    # ----------------------------------------------------------------------
    # UI callbacks for supersampling / cache toggles are now in Settings
    # ----------------------------------------------------------------------
    def on_supersample_toggled(self, widget_or_bool):
        global SUPERSAMPLE_ENABLED
        # widget_or_bool may be a CheckButton or a boolean
        if isinstance(widget_or_bool, Gtk.CheckButton):
            SUPERSAMPLE_ENABLED = widget_or_bool.get_active()
        else:
            SUPERSAMPLE_ENABLED = bool(widget_or_bool)
        save_config()

    def on_supersample_factor_changed(self, widget_or_value):
        global SUPERSAMPLE_FACTOR
        try:
            if isinstance(widget_or_value, Gtk.SpinButton):
                SUPERSAMPLE_FACTOR = int(widget_or_value.get_value_as_int())
            else:
                SUPERSAMPLE_FACTOR = int(widget_or_value)
        except Exception:
            SUPERSAMPLE_FACTOR = 3
        save_config()

    def on_disk_cache_toggled(self, widget_or_bool):
        global DISK_CACHE_ENABLED
        if isinstance(widget_or_bool, Gtk.CheckButton):
            DISK_CACHE_ENABLED = widget_or_bool.get_active()
        else:
            DISK_CACHE_ENABLED = bool(widget_or_bool)
        if DISK_CACHE_ENABLED:
            ensure_disk_cache_dir()
        save_config()

    def _start_export_progress(self, total: int, title: str = "Exporting icons..."):
        """
        Create and show a modal progress dialog. Called from background thread via GLib.idle_add.
        """
        def _create():
            # destroy existing if present
            try:
                if self._export_progress_dialog:
                    self._export_progress_dialog.destroy()
            except Exception:
                pass

            dlg = Gtk.Dialog(title=title, transient_for=self, flags=0)
            dlg.set_default_size(420, 110)
            content = dlg.get_content_area()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=10)
            content.pack_start(box, True, True, 0)

            lbl = Gtk.Label(label="Preparing export...", xalign=0)
            box.pack_start(lbl, False, False, 0)

            prog = Gtk.ProgressBar()
            prog.set_show_text(True)
            prog.set_fraction(0.0)
            box.pack_start(prog, False, False, 0)

            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            cancel_btn = Gtk.Button(label="Cancel")
            btn_box.pack_end(cancel_btn, False, False, 0)
            box.pack_start(btn_box, False, False, 0)

            def _on_cancel(bt):
                with self._export_lock:
                    self._export_cancel_requested = True
                cancel_btn.set_sensitive(False)
                lbl.set_text("Cancel requested — finishing current task...")

            cancel_btn.connect("clicked", _on_cancel)

            dlg.show_all()

            self._export_progress_dialog = dlg
            self._export_progress_bar = prog
            self._export_progress_label = lbl
            self._export_total_tasks = int(total) if total else 0
            self._export_done_tasks = 0
            with self._export_lock:
                self._export_cancel_requested = False
            return False

        GLib.idle_add(_create)


    def _update_export_progress(self, done: int, message: Optional[str] = None):
        """
        Update progress bar and label. safe to call from background threads.
        """
        def _upd():
            if not self._export_progress_bar:
                return False
            try:
                self._export_done_tasks = int(done)
                total = float(self._export_total_tasks) if self._export_total_tasks else 1.0
                frac = min(max(self._export_done_tasks / total, 0.0), 1.0)
                self._export_progress_bar.set_fraction(frac)
                text = f"{self._export_done_tasks}/{self._export_total_tasks}"
                if message:
                    self._export_progress_bar.set_text(f"{text} — {message}")
                    self._export_progress_label.set_text(message)
                else:
                    self._export_progress_bar.set_text(text)
            except Exception:
                pass
            return False

        GLib.idle_add(_upd)


    def _finish_export_progress(self):
        """
        Close progress dialog and reset flags.
        """
        def _finish():
            try:
                if self._export_progress_dialog:
                    self._export_progress_dialog.destroy()
            except Exception:
                pass
            self._export_progress_dialog = None
            self._export_progress_bar = None
            self._export_progress_label = None
            self._export_total_tasks = 0
            self._export_done_tasks = 0
            with self._export_lock:
                self._export_cancel_requested = False
            return False

        GLib.idle_add(_finish)

    # ----------------------------------------------------------------------
    # Settings dialog: central place for supersample, disk cache, install, and Mint-Y export options
    # ----------------------------------------------------------------------
    def on_settings_clicked(self, widget):
        # Declare globals up-front. They must appear before any use in this function.
        global SUPERSAMPLE_ENABLED, SUPERSAMPLE_FACTOR
        global DISK_CACHE_ENABLED, DISK_CACHE_DIR, DISK_CACHE_SIZE_LIMIT
        global MINTY_ENABLED, MINTY_EXPORT_PATH, MINTY_2X_ENABLED

        dialog = Gtk.Dialog(title="Settings", transient_for=self, flags=0)
        dialog.set_default_size(600, 400)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK)

        content = dialog.get_content_area()
        notebook = Gtk.Notebook()
        content.pack_start(notebook, True, True, 0)

        # General tab (supersampling and disk cache)
        gen_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=10)
        ss_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        supersample_check = Gtk.CheckButton(label="Enable supersampling")
        supersample_check.set_active(bool(SUPERSAMPLE_ENABLED))
        supersample_check.connect("toggled", lambda w: None)
        ss_box.pack_start(supersample_check, False, False, 0)
        adj = Gtk.Adjustment(value=SUPERSAMPLE_FACTOR, lower=2, upper=6, step_increment=1)
        supersample_spin = Gtk.SpinButton(adjustment=adj, numeric=True)
        supersample_spin.set_value(SUPERSAMPLE_FACTOR)
        ss_box.pack_start(Gtk.Label(label="Factor:"), False, False, 0)
        ss_box.pack_start(supersample_spin, False, False, 0)
        gen_box.pack_start(ss_box, False, False, 0)

        cache_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        disk_cache_check = Gtk.CheckButton(label="Use disk thumbnail cache")
        disk_cache_check.set_active(bool(DISK_CACHE_ENABLED))
        cache_box.pack_start(disk_cache_check, False, False, 0)
        cache_size_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cache_size_label = Gtk.Label(label="Disk cache limit (MB):")
        # Use current global value here; global declared earlier in function
        cache_size_adj = Gtk.Adjustment(value=DISK_CACHE_SIZE_LIMIT // (1024*1024), lower=10, upper=10240, step_increment=10)
        cache_size_spin = Gtk.SpinButton(adjustment=cache_size_adj, numeric=True)
        cache_size_box.pack_start(cache_size_label, False, False, 0)
        cache_size_box.pack_start(cache_size_spin, False, False, 0)
        cache_box.pack_start(cache_size_box, False, False, 0)
        gen_box.pack_start(cache_box, False, False, 0)

        notebook.append_page(gen_box, Gtk.Label(label="General"))

        # Mint-Y Export tab
        mint_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=10)
        mint_enable = Gtk.CheckButton(label="Use Mint-Y export style (master SVG with rects)")
        mint_enable.set_active(bool(MINTY_ENABLED))
        mint_box.pack_start(mint_enable, False, False, 0)

        path_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        path_label = Gtk.Label(label="Export root folder:")
        # Use a folder chooser button
        chooser = Gtk.FileChooserButton(title="Select export root folder", action=Gtk.FileChooserAction.SELECT_FOLDER)
        if MINTY_EXPORT_PATH:
            try:
                chooser.set_filename(MINTY_EXPORT_PATH)
            except Exception:
                pass
        path_box.pack_start(path_label, False, False, 0)
        path_box.pack_start(chooser, True, True, 0)
        mint_box.pack_start(path_box, False, False, 0)

        # 2x toggle
        mint_2x = Gtk.CheckButton(label="Generate @2x HiDPI variants (e.g. 16@2x)")
        mint_2x.set_active(bool(MINTY_2X_ENABLED))
        mint_box.pack_start(mint_2x, False, False, 0)

        # Example sizes info
        sizes_label = Gtk.Label(label="Typical Mint-Y sizes produced: 16 22 24 256 32 48 64 96 and @2x variants", xalign=0)
        mint_box.pack_start(sizes_label, False, False, 0)

        # Install theme button (exposed here)
        install_btn = Gtk.Button(label="Install Theme Now")
        install_btn.connect("clicked", self.on_install_theme_clicked)
        mint_box.pack_start(install_btn, False, False, 0)

        notebook.append_page(mint_box, Gtk.Label(label="Mint-Y Export"))

        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            # Apply general settings
            # Note: these functions will call save_config()
            self.on_supersample_toggled(supersample_check)
            self.on_supersample_factor_changed(supersample_spin)
            self.on_disk_cache_toggled(disk_cache_check)
            try:
                DISK_CACHE_SIZE_LIMIT = int(cache_size_spin.get_value_as_int()) * 1024 * 1024
            except Exception:
                pass

            # Apply mint settings (globals declared at top of function)
            MINTY_ENABLED = mint_enable.get_active()
            chosen = chooser.get_filename()
            MINTY_EXPORT_PATH = chosen or ""
            MINTY_2X_ENABLED = mint_2x.get_active()
            save_config()
            # update label
            self.export_mode_label.set_text(self._export_mode_text())
            # ensure disk cache dir if enabled
            if DISK_CACHE_ENABLED:
                ensure_disk_cache_dir()
        dialog.destroy()

    # ----------------------------------------------------------------------
    # Dialogs and Messaging
    # ----------------------------------------------------------------------
    def show_message(self, title: str, message: str):
        md = Gtk.MessageDialog(parent=self, flags=0, message_type=Gtk.MessageType.INFO,
                               buttons=Gtk.ButtonsType.OK, text=title)
        md.format_secondary_text(message)
        md.run()
        md.destroy()

    # ----------------------------------------------------------------------
    # Theme folder selection & indexing
    # ----------------------------------------------------------------------
    def on_choose_theme(self, widget):
        dialog = Gtk.FileChooserDialog(
            title="Select Icon Theme Folder",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                           Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self.theme_path = dialog.get_filename()
            self.install_btn = None
            close_all_previews()
            clear_pixbuf_cache()
            self.indexing_done = False
            self.category_list.set_sensitive(False)
            self.create_symlink_btn.set_sensitive(True)
            threading.Thread(target=self.index_theme_icons, daemon=True).start()
        dialog.destroy()

    def index_theme_icons(self):
        if not self.theme_path:
            return
        icons_found = {}
        for root, dirs, files in os.walk(self.theme_path):
            for f in files:
                if f.endswith((".svg", ".png")):
                    icon_name, ext = os.path.splitext(f)
                    if icon_name not in icons_found:
                        icons_found[icon_name] = {'svg': None, 'pngs': []}
                    full_path = os.path.join(root, f)
                    if ext.lower() == ".svg":
                        icons_found[icon_name]['svg'] = full_path
                    elif ext.lower() == ".png":
                        parent_dir = os.path.basename(root)
                        try:
                            size = int(parent_dir.split('@')[0])  # handle "@2x" suffix directories if present
                        except ValueError:
                            size = 0
                        icons_found[icon_name]['pngs'].append((size, full_path))
        idx = {}
        for icon_name, sources in icons_found.items():
            if sources['svg']:
                idx[icon_name] = sources['svg']
            elif sources['pngs']:
                largest_png = max(sources['pngs'], key=lambda t: t[0])
                idx[icon_name] = largest_png[1]
        GLib.idle_add(self.update_icon_index, idx)

    def update_icon_index(self, idx: Dict[str, str]):
        self.icon_index = idx
        self.indexing_done = True
        self.category_list.set_sensitive(True)
        if not self.current_category and self.icon_categories:
            first_row = self.category_list.get_row_at_index(0)
            self.category_list.select_row(first_row)
            self.current_category = first_row.get_child().get_text()
        if self.current_category:
            self.load_icons(self.current_category)
        return False

    # ----------------------------------------------------------------------
    # Search / filter / virtualization helpers
    # ----------------------------------------------------------------------
    def on_search_changed(self, entry):
        close_all_previews()
        try:
            if hasattr(self, "_search_timeout_id") and self._search_timeout_id is not None:
                GLib.source_remove(self._search_timeout_id)
        except Exception:
            pass
        self._pending_search_text = entry.get_text().strip().lower()
        def _do_search():
            self.search_text = getattr(self, "_pending_search_text", "").lower()
            if self.current_category:
                self.load_icons(self.current_category)
            self._search_timeout_id = None
            return False
        self._search_timeout_id = GLib.timeout_add(200, _do_search)

    def on_status_filter_changed(self, combo):
        close_all_previews()
        self.current_status_filter = combo.get_active_text()
        if self.current_category:
            self.load_icons(self.current_category)

    def on_category_selected(self, listbox, row):
        if not row or not self.indexing_done:
            return
        close_all_previews()
        category_name = row.get_child().get_text()
        self.current_category = category_name
        self.load_icons(category_name)

    def on_scroll_adjustment(self, adj):
        upper = adj.get_upper()
        value = adj.get_value()
        page_size = adj.get_page_size()
        if value + page_size >= upper - 200:
            GLib.idle_add(self._load_next_page)

    def _prepare_filtered_list(self, category_name: str) -> List[str]:
        icon_names = self.icon_categories.get(category_name, [])
        if self.search_text:
            icon_names = [name for name in icon_names if self.search_text in name.lower()]
        filtered = []
        for icon_name in icon_names:
            icon_path = self.icon_index.get(icon_name)
            csf = getattr(self, "current_status_filter", "All Icons")
            if csf == "All Icons":
                filtered.append(icon_name)
            elif csf == "Missing Icons":
                if not icon_path or not os.path.isfile(icon_path):
                    filtered.append(icon_name)
            elif csf == "Only SVG":
                if icon_path and icon_path.lower().endswith(".svg"):
                    filtered.append(icon_name)
            elif csf == "Only PNG":
                if icon_path and icon_path.lower().endswith(".png"):
                    filtered.append(icon_name)
            elif csf == "Symlinks Only":
                if icon_path and os.path.islink(icon_path):
                    filtered.append(icon_name)
            elif csf == "All Except Symlinks":
                if not icon_path or not os.path.islink(icon_path):
                    filtered.append(icon_name)
            elif csf == "Large Files":
                if icon_path and ((icon_path.lower().endswith(".svg") and os.path.getsize(icon_path) > 1024*1024) or
                                  (icon_path.lower().endswith(".png") and os.path.getsize(icon_path) > 1024*1024)):
                    filtered.append(icon_name)
        return filtered

    def load_icons(self, category_name: str):
        close_all_previews()
        try:
            existing_children = list(self.flowbox.get_children())
        except Exception:
            existing_children = []
        for child in existing_children:
            try:
                if hasattr(child, "cancel_hover"):
                    child.cancel_hover()
            except Exception:
                pass
            try:
                if hasattr(child, "hide_enlarged_preview"):
                    child.hide_enlarged_preview()
            except Exception:
                pass
            try:
                self.flowbox.remove(child)
            except Exception:
                pass
            try:
                child.destroy()
            except Exception:
                pass
        self.icon_boxes.clear()
        self._current_filtered_list = self._prepare_filtered_list(category_name)
        self._page_loaded_until = 0
        GLib.idle_add(self._load_next_page)
        self.show_all()

    def _load_next_page(self):
        start = self._page_loaded_until
        end = min(len(self._current_filtered_list), start + ICON_PAGE_SIZE)
        if start >= end:
            return False
        for i in range(start, end):
            icon_name = self._current_filtered_list[i]
            icon_path = self.icon_index.get(icon_name, PLACEHOLDER_PATH)

            box = LazyIconBox(icon_name, icon_path, self.on_icon_clicked)

            box.icon_helper = self
            try:
                box.update_icon(icon_path)
            except Exception:
                pass

            self.flowbox.add(box)
            self.icon_boxes.append(box)
        self._page_loaded_until = end
        self.show_all()
        return False

    # ----------------------------------------------------------------------
    # Helpers for Mint-Y style parsing and rendering
    # ----------------------------------------------------------------------
    def _parse_master_svg(self, svg_path: str):
        """
        Parse an SVG similar to Mint-Y / Moka master files.

        Two-pass approach:
        1) SAX-based detection of a 'Baseplate' layer and rects inside it (fast/strict).
        2) Fallback: ElementTree scan of the whole document for any <rect>-like element
        and for id patterns containing sizes (robust).

        Returns dict with keys: icon_name (str), context (str), rects (list of dicts {id,width,height})
        or None if not detected.
        """
        import re
        import xml.sax
        from xml.etree import ElementTree as ET
        class MasterHandler(xml.sax.ContentHandler):
            ROOT = 0
            SVG = 1
            LAYER = 2
            OTHER = 3
            TEXT = 4

            def __init__(self):
                super().__init__()
                self.stack = [self.ROOT]
                self.inside = [self.ROOT]
                self.rects = []
                self.chars = ""
                self.context = None
                self.icon_name = None
                self.text = None

            def _looks_like_baseplate(self, attrs):
                # Check any attribute value that contains 'baseplate' (case-insensitive)
                for k in attrs.getNames():
                    try:
                        v = attrs.get(k)
                        if v and 'baseplate' in v.strip().lower():
                            return True
                    except Exception:
                        pass
                # Also check for layer markers: inkscape:groupmode="layer" + label containing baseplate
                try:
                    gm = False
                    label = None
                    for k in attrs.getNames():
                        kn = k.lower()
                        if kn.endswith('groupmode') and attrs.get(k) and attrs.get(k).lower() == 'layer':
                            gm = True
                        if kn.endswith('label'):
                            label = attrs.get(k)
                    if gm and label and 'baseplate' in label.strip().lower():
                        return True
                except Exception:
                    pass
                return False

            def _parse_int_like(self, s):
                if not s:
                    return None
                s2 = re.sub(r'[^\d\.]', '', s)
                if not s2:
                    return None
                try:
                    return int(float(s2))
                except Exception:
                    return None

            def startElement(self, name, attrs):
                if self.inside[-1] == self.ROOT:
                    if name == "svg":
                        self.stack.append(self.SVG)
                        self.inside.append(self.SVG)
                        return
                elif self.inside[-1] == self.SVG:
                    if self._looks_like_baseplate(attrs):
                        self.stack.append(self.LAYER)
                        self.inside.append(self.LAYER)
                        self.rects = []
                        self.context = None
                        self.icon_name = None
                        return
                elif self.inside[-1] == self.LAYER:
                    for k in attrs.getNames():
                        try:
                            if attrs.get(k) and attrs.get(k).strip().lower() == 'context':
                                self.stack.append(self.TEXT)
                                self.inside.append(self.TEXT)
                                self.text = 'context'
                                self.chars = ""
                                return
                            if attrs.get(k) and attrs.get(k).strip().lower() == 'icon-name':
                                self.stack.append(self.TEXT)
                                self.inside.append(self.TEXT)
                                self.text = 'icon-name'
                                self.chars = ""
                                return
                        except Exception:
                            pass
                    if name == "rect":
                        rect_id = attrs.get("id", None)
                        width = attrs.get("width", None)
                        height = attrs.get("height", None)
                        w = self._parse_int_like(width) if width is not None else None
                        h = self._parse_int_like(height) if height is not None else None

                        # infer from id using common patterns (rect16x16, rect_48, 16@2x, 96, etc.)
                        if (not w or w == 0) and rect_id:
                            m = re.search(r'(\d{1,4})\s*[x×]\s*(\d{1,4})', rect_id)
                            if m:
                                try:
                                    w = int(m.group(1)); h = int(m.group(2))
                                except Exception:
                                    pass
                            if not w:
                                m2 = re.search(r'(\d{2,4})(?:@(\d)x)?', rect_id)
                                if m2:
                                    try:
                                        w = int(m2.group(1))
                                    except Exception:
                                        pass
                            if not w:
                                m3 = re.search(r'[_\-\s](\d{2,4})(?:@(\d)x)?', rect_id)
                                if m3:
                                    try:
                                        w = int(m3.group(1))
                                    except Exception:
                                        pass
                        if rect_id and w and w > 0:
                            self.rects.append({"id": rect_id, "width": w, "height": h or w})
                            return
                self.stack.append(self.OTHER)

            def endElement(self, name):
                if not self.stack:
                    return
                stacked = self.stack.pop()
                if self.inside and self.inside[-1] == stacked:
                    self.inside.pop()
                if stacked == self.TEXT and self.text is not None:
                    if self.text == 'context':
                        self.context = (self.chars or "").strip()
                    elif self.text == 'icon-name':
                        self.icon_name = (self.chars or "").strip()
                    self.text = None

            def characters(self, content):
                if getattr(self, 'text', None):
                    self.chars += content

        handler = MasterHandler()
        try:
            xml.sax.parse(svg_path, handler)
        except Exception:
            # ignore SAX parse errors and continue to fallback
            pass

        rects = handler.rects[:]
        icon_name = handler.icon_name
        context = handler.context or "apps"

        # If SAX didn't find multiple rects (common in some variants), do a robust fallback:
        if len(rects) < 2:
            try:
                tree = ET.parse(svg_path)
                root = tree.getroot()
                # namespace-insensitive iteration: tag may be '{...}rect'
                found = {}
                for elem in root.iter():
                    tag = elem.tag
                    if isinstance(tag, str) and tag.lower().endswith('rect'):
                        rid = elem.get('id') or elem.get('{http://www.w3.org/XML/1998/namespace}id')
                        w = None
                        h = None
                        # try width/height attributes
                        ww = elem.get('width') or elem.get('{http://www.w3.org/1999/xlink}width')
                        hh = elem.get('height') or elem.get('{http://www.w3.org/1999/xlink}height')
                        if ww:
                            try:
                                w = int(float(re.sub(r'[^\d\.]', '', ww)))
                            except Exception:
                                w = None
                        if hh:
                            try:
                                h = int(float(re.sub(r'[^\d\.]', '', hh)))
                            except Exception:
                                h = None
                        # infer from id if needed
                        if (not w or w == 0) and rid:
                            m = re.search(r'(\d{1,4})\s*[x×]\s*(\d{1,4})', rid)
                            if m:
                                try:
                                    w = int(m.group(1)); h = int(m.group(2))
                                except Exception:
                                    pass
                            if not w:
                                m2 = re.search(r'(\d{2,4})(?:@(\d)x)?', rid)
                                if m2:
                                    try:
                                        w = int(m2.group(1))
                                    except Exception:
                                        pass
                            if not w:
                                m3 = re.search(r'[_\-\s](\d{2,4})(?:@(\d)x)?', rid)
                                if m3:
                                    try:
                                        w = int(m3.group(1))
                                    except Exception:
                                        pass
                        if rid and w and w > 0:
                            found[rid] = {"id": rid, "width": w, "height": h or w}
                # Merge unique found rects into rects list, preserving any SAX results first
                for r in found.values():
                    if not any(existing['id'] == r['id'] for existing in rects):
                        rects.append(r)
                # Try also scanning for <g> or other elements with ids that encode sizes (some templates place markers as groups)
                if len(rects) < 2:
                    for elem in root.iter():
                        rid = elem.get('id')
                        if not rid:
                            continue
                        m = re.search(r'(\d{2,4})(?:@(\d)x)?', rid)
                        if m:
                            try:
                                w = int(m.group(1))
                                if not any(existing['id'] == rid for existing in rects):
                                    rects.append({"id": rid, "width": w, "height": w})
                            except Exception:
                                pass
                # try to extract icon-name if missing: look for elements with text in a label attribute
                if not icon_name:
                    # look for <text> elements whose parent's attributes include 'icon-name' or similar
                    for elem in root.iter():
                        tag = elem.tag
                        if isinstance(tag, str) and tag.lower().endswith('text'):
                            # element text
                            txt = (elem.text or "").strip()
                            if txt and len(txt) > 0 and 'icon' in txt.lower():
                                # crude heuristics: skip, prefer label attribute searching instead
                                pass
                    # also search for any attribute value that equals an icon name (rare)
            except Exception:
                pass

        # final sanity: need an icon_name and at least one rect to consider a valid master
        if not icon_name:
            # try to infer icon_name from filename
            icon_name = os.path.splitext(os.path.basename(svg_path))[0]

        if rects:
            return {"icon_name": icon_name, "context": context, "rects": rects}
        return None

    def _inkscape_render_rect(self, svg_file, rect_id, dpi, output_file):
        """
        Render an individual rect (by id) from svg_file with inkscape.
        Try multiple CLI variants and capture stdout/stderr for diagnostics.
        Returns True on success, False on failure.
        """
        # detect inkscape binary and flatpak presence
        inkscape_bin = shutil.which("inkscape") or "inkscape"
        flatpak_bin = shutil.which("flatpak")

        cmds = []

        # If flatpak is available, try calling Inkscape via flatpak run org.inkscape.Inkscape
        if flatpak_bin:
            # Try a couple of flatpak wrapped variants
            cmds.append([flatpak_bin, "run", "org.inkscape.Inkscape", "--batch-process", "--export-dpi", str(dpi), "-i", rect_id, "-o", output_file, svg_file])
            cmds.append([flatpak_bin, "run", "org.inkscape.Inkscape", svg_file, f"--export-filename={output_file}", f"--export-dpi={dpi}", f"--export-id={rect_id}", "--export-id-only"])
            cmds.append([flatpak_bin, "run", "org.inkscape.Inkscape", "--export-id", rect_id, f"--export-filename={output_file}", f"--export-dpi={dpi}", svg_file])

        # native inkscape variants
        cmds.append([inkscape_bin, "--batch-process", "--export-dpi", str(dpi), "-i", rect_id, "-o", output_file, svg_file])
        cmds.append([inkscape_bin, svg_file, f"--export-filename={output_file}", f"--export-dpi={dpi}", f"--export-id={rect_id}", "--export-id-only"])
        cmds.append([inkscape_bin, "--export-id", rect_id, f"--export-filename={output_file}", f"--export-dpi={dpi}", svg_file])

        for cmd in cmds:
            try:
                proc = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
                return True
            except subprocess.CalledProcessError as e:
                print(f"Inkscape export failed (exit {e.returncode}): {' '.join(cmd)}")
                if e.stdout:
                    print("stdout:", e.stdout)
                if e.stderr:
                    print("stderr:", e.stderr)
                # try next variant
            except FileNotFoundError:
                print(f"Inkscape binary not found (tried {' '.join(cmd)}).")
                # try next variant
            except subprocess.TimeoutExpired:
                print(f"Inkscape export timed out: {' '.join(cmd)}")
            except Exception as e:
                print(f"Inkscape export error for {' '.join(cmd)}: {e}")

        print(f"All Inkscape export attempts failed for id={rect_id} svg={svg_file} -> {output_file}")
        return False

    def _resolve_export_root(self):
        """
        Resolve the effective export root for Mint-Y style exports.
        If MINTY_EXPORT_PATH is set and absolute, use it. If it's set and relative,
        resolve it against the currently selected theme_path. Otherwise fall back to theme_path.
        """
        if MINTY_EXPORT_PATH:
            if os.path.isabs(MINTY_EXPORT_PATH):
                return MINTY_EXPORT_PATH
            elif self.theme_path:
                return os.path.join(self.theme_path, MINTY_EXPORT_PATH)
            else:
                return MINTY_EXPORT_PATH
        return self.theme_path

    # ----------------------------------------------------------------------
    # Icon editing / bitmap generation (Mint-Y support included)
    # ----------------------------------------------------------------------
    def on_icon_clicked(self, icon_path: str, icon_name: str):
        """
        Open the SVG for editing. Prefer the SVG path shown in the grid (icon_path)
        when it is an actual SVG file. Otherwise create/copy a new SVG into the
        theme (category/96) from TEMPLATE_SVG and open that.
        """
        if not self.theme_path or not self.current_category:
            self.show_message("Error", "Theme or category not selected")
            return

        # Prefer to edit the actual SVG shown in the grid if it exists.
        svg_to_edit = None
        try:
            if icon_path and os.path.isfile(icon_path) and icon_path.lower().endswith(".svg"):
                # Don't open the placeholder or template as the "existing" source.
                if os.path.abspath(icon_path) != os.path.abspath(PLACEHOLDER_PATH) and os.path.abspath(icon_path) != os.path.abspath(TEMPLATE_SVG):
                    svg_to_edit = icon_path
        except Exception:
            svg_to_edit = None

        # If no existing svg found (or it's a placeholder), create/use one inside the theme (96)
        if not svg_to_edit:
            base_dir = os.path.join(self.theme_path, self.current_category, "96")
            if not os.path.isdir(base_dir):
                base_dir = os.path.join(self.theme_path, "fallback", "96")
            os.makedirs(base_dir, exist_ok=True)
            new_icon_path = os.path.join(base_dir, icon_name + ".svg")
            if not os.path.exists(new_icon_path):
                if not check_file_exists(TEMPLATE_SVG):
                    self.show_message("Error", f"Missing SVG template: {TEMPLATE_SVG}")
                    return
                try:
                    shutil.copy2(TEMPLATE_SVG, new_icon_path)
                except Exception as e:
                    self.show_message("Error", f"Failed to copy template: {e}")
                    return
            svg_to_edit = new_icon_path

        # Launch editor on the chosen svg and start watching it for changes
        try:
            subprocess.Popen(["inkscape", svg_to_edit])
        except Exception as e:
            self.show_message("Error", f"Failed to launch Inkscape: {e}")
            return

        # Watch the exact file opened so we regenerate bitmaps for the correct source
        threading.Thread(target=self.watch_and_generate, args=(svg_to_edit,), daemon=True).start()

    def watch_and_generate(self, svg_path: str):
        try:
            last_mtime = os.path.getmtime(svg_path)
        except Exception:
            return
        while True:
            time.sleep(2)
            try:
                mtime = os.path.getmtime(svg_path)
            except FileNotFoundError:
                break
            if mtime != last_mtime:
                last_mtime = mtime
                # Backup the svg before processing changes
                try:
                    self.backup_svg(svg_path)
                except Exception:
                    pass
                # debounce briefly to allow multiple quick writes to settle
                time.sleep(0.5)
                self.generate_bitmaps(svg_path)
                GLib.idle_add(self.refresh_icon, os.path.basename(svg_path))
                break

    def generate_bitmaps(self, svg_path: str):
        """
        If the SVG resembles a Mint-Y master (has Baseplate with rects), export using rect ids
        into size folders (and optionally @2x). Otherwise fall back to per-size generation (Mint-X style).

        This version filters/matches the discovered rect widths to a known set of standard icon sizes
        (to avoid creating folders like 88, 220, etc). If a rect width is close to a standard size
        (within a tolerance) it is snapped to that size; otherwise it is ignored.
        """
        base_name = os.path.splitext(os.path.basename(svg_path))[0]
        # Detect master layout
        try:
            master = self._parse_master_svg(svg_path)
        except Exception:
            master = None

        # Standard sizes Mint-Y typically produces
        STANDARD_SIZES = [16, 22, 24, 32, 48, 64, 96, 256]
        TOLERANCE = 0.15  # relative tolerance to snap non-standard widths to a standard size (15%)

        def snap_to_standard(w: int) -> Optional[int]:
            if not w:
                return None
            if w in STANDARD_SIZES:
                return w
            # find closest
            best = None
            best_diff = None
            for s in STANDARD_SIZES:
                diff = abs(s - w) / float(s)
                if best is None or diff < best_diff:
                    best = s
                    best_diff = diff
            if best is not None and best_diff is not None and best_diff <= TOLERANCE:
                return best
            return None

        if master and MINTY_ENABLED:
            try:
                icon_name = master.get("icon_name", base_name)
                context = master.get("context", self.current_category or "apps")
                export_root = self._resolve_export_root() or self.theme_path
                dpi_factors = [1] + ([2] if MINTY_2X_ENABLED else [])

                # Build mapping snapped to standard sizes (same logic you already have)
                rects = master.get("rects", [])
                mapped: Dict[int, str] = {}
                for rect in rects:
                    rid = rect.get("id")
                    width = rect.get("width") or rect.get("height") or 0
                    snapped = snap_to_standard(width)  # your existing snap function
                    if snapped and snapped not in mapped:
                        mapped[snapped] = rid

                # prepare tasks count
                total_tasks = len(mapped) * len(dpi_factors)
                # start progress UI
                self._start_export_progress(total_tasks, title=f"Exporting {icon_name} icons")

                done = 0
                try:
                    for size, rid in sorted(mapped.items()):
                        for factor in dpi_factors:
                            # allow cancelation check before starting next task
                            with self._export_lock:
                                if self._export_cancel_requested:
                                    raise KeyboardInterrupt("Export cancelled by user")
                            dpi = DPI_1_TO_1 * factor
                            size_str = f"{size}" if factor == 1 else f"{size}@{factor}x"
                            out_dir = os.path.join(export_root, context, size_str)
                            os.makedirs(out_dir, exist_ok=True)
                            outfile = os.path.join(out_dir, icon_name + ".png")

                            needs = False
                            if not os.path.exists(outfile):
                                needs = True
                            else:
                                stat_in = os.stat(svg_path)
                                stat_out = os.stat(outfile)
                                if stat_in.st_mtime > stat_out.st_mtime:
                                    needs = True

                            if needs:
                                ok = self._inkscape_render_rect(svg_path, rid, dpi, outfile)
                                # optionally log ok/fail
                            # one task completed regardless of success
                            done += 1
                            self._update_export_progress(done, f"{size_str} -> {os.path.basename(outfile)}")
                    # finished normally
                except KeyboardInterrupt:
                    # user cancelled — fall through to cleanup
                    pass
                finally:
                    # ensure caches refreshed
                    try:
                        for size in mapped.keys():
                            # touch invalidation for each output size variant
                            for factor in dpi_factors:
                                size_str = f"{size}" if factor == 1 else f"{size}@{factor}x"
                                out_dir = os.path.join(export_root, context, size_str)
                                outfile = os.path.join(out_dir, icon_name + ".png")
                                invalidate_pixbuf_cache_for_path(outfile)
                    except Exception:
                        pass
                    # finish UI
                    self._finish_export_progress()

                # update index and UI
                self.icon_index[base_name] = svg_path
                if self.current_category:
                    GLib.idle_add(self.load_icons, self.current_category)
            except Exception as e:
                print(f"Master export failed for {svg_path}: {e}")
            return

        # Fallback: Mint-X style -> generate per BITMAP_SIZES
        category = next((cat for cat, icons in self.icon_categories.items() if base_name in icons), "fallback")
        for size in BITMAP_SIZES:
            out_dir = os.path.join(self.theme_path, category, str(size))
            os.makedirs(out_dir, exist_ok=True)
            out_png = os.path.join(out_dir, base_name + ".png")
            use_supersample = SUPERSAMPLE_ENABLED and SUPERSAMPLE_FACTOR > 1
            export_size = size * SUPERSAMPLE_FACTOR if use_supersample else size
            if use_supersample:
                tmp_file = None
                try:
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                    tmp_file = tmp.name
                    tmp.close()
                    cmd = ["inkscape", svg_path, f"--export-filename={tmp_file}", f"--export-width={export_size}", f"--export-height={export_size}"]
                    subprocess.run(cmd, check=True)
                    try:
                        from PIL import Image
                        with Image.open(tmp_file) as im:
                            im = im.resize((size, size), Image.LANCZOS)
                            # write to temp then move atomically
                            tmp_out = out_png + ".tmp.png"
                            im.save(tmp_out)
                            os.replace(tmp_out, out_png)
                    except Exception:
                        try:
                            tmp_out = out_png + ".tmp.png"
                            subprocess.run(["convert", tmp_file, "-filter", "Lanczos", "-resize", f"{size}x{size}", tmp_out], check=True)
                            os.replace(tmp_out, out_png)
                        except Exception:
                            try:
                                os.replace(tmp_file, out_png)
                                tmp_file = None
                            except Exception as e:
                                print(f"Failed to downscale or move temporary export: {e}")
                except subprocess.CalledProcessError as e:
                    print(f"Failed to export bitmap for size {size}: {e}")
                finally:
                    if tmp_file and os.path.exists(tmp_file):
                        try:
                            os.remove(tmp_file)
                        except Exception:
                            pass
            else:
                tmp_out = out_png + ".tmp.png"
                cmd = ["inkscape", svg_path, f"--export-filename={tmp_out}", f"--export-width={size}", f"--export-height={size}"]
                try:
                    subprocess.run(cmd, check=True)
                    # move tmp to final atomically if created
                    if os.path.exists(tmp_out):
                        os.replace(tmp_out, out_png)
                except subprocess.CalledProcessError as e:
                    print(f"Failed to export bitmap for size {size}: {e}")
                except Exception:
                    try:
                        if os.path.exists(tmp_out):
                            os.replace(tmp_out, out_png)
                    except Exception:
                        pass
            try:
                invalidate_pixbuf_cache_for_path(out_png)
                invalidate_pixbuf_cache_for_path(svg_path)
            except Exception:
                pass
        self.icon_index[base_name] = svg_path
        if self.current_category:
            GLib.idle_add(self.load_icons, self.current_category)

    def refresh_icon(self, changed_filename: str):
        changed_path = None
        for name, path in self.icon_index.items():
            if os.path.basename(path) == changed_filename or os.path.basename(path) == os.path.basename(changed_filename):
                changed_path = path
                break
        if changed_path:
            invalidate_pixbuf_cache_for_path(changed_path)
        if self.current_category:
            self.load_icons(self.current_category)
        return False

    # ----------------------------------------------------------------------
    # Symlink dialog & logic (unchanged)
    # ----------------------------------------------------------------------
    def on_create_symlink_clicked(self, button):
        dialog = Gtk.Dialog(title="Create Symlink", transient_for=self, flags=0)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK)
        dialog.set_default_size(400, 300)
        box = dialog.get_content_area()
        grid = Gtk.Grid(column_spacing=10, row_spacing=10, margin=10)
        box.add(grid)
        cat_label = Gtk.Label(label="Select Category (symlink location):", halign=Gtk.Align.START)
        grid.attach(cat_label, 0, 0, 1, 1)
        category_store = Gtk.ListStore(str)
        for cat in sorted(self.icon_categories.keys()):
            category_store.append([cat])
        category_combo = Gtk.ComboBox.new_with_model(category_store)
        renderer_text = Gtk.CellRendererText()
        category_combo.pack_start(renderer_text, True)
        category_combo.add_attribute(renderer_text, "text", 0)
        category_combo.set_active(0)
        grid.attach(category_combo, 1, 0, 1, 1)
        src_label = Gtk.Label(label="Symlink Source (icon base name):", halign=Gtk.Align.START)
        grid.attach(src_label, 0, 1, 1, 1)
        src_entry = Gtk.Entry()
        src_entry.set_placeholder_text("e.g. cool-mimetype")
        grid.attach(src_entry, 1, 1, 1, 1)
        src_example = Gtk.Label(label="Example: 'cool-mimetype' (no extension)", halign=Gtk.Align.START)
        src_example.get_style_context().add_class("dim-label")
        grid.attach(src_example, 1, 2, 1, 1)
        tgt_label = Gtk.Label(label="Symlink Target (new icon base name):", halign=Gtk.Align.START)
        grid.attach(tgt_label, 0, 3, 1, 1)
        tgt_entry = Gtk.Entry()
        tgt_entry.set_placeholder_text("e.g. evencooler-mimetype")
        grid.attach(tgt_entry, 1, 3, 1, 1)
        tgt_example = Gtk.Label(label="Example: 'evencooler-mimetype' (no extension)", halign=Gtk.Align.START)
        tgt_example.get_style_context().add_class("dim-label")
        grid.attach(tgt_example, 1, 4, 1, 1)
        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            category_iter = category_combo.get_active_iter()
            if category_iter is not None:
                category = category_store[category_iter][0]
                src_name = src_entry.get_text().strip()
                tgt_name = tgt_entry.get_text().strip()
                if not src_name or not tgt_name:
                    self.show_message("Error", "Source and Target cannot be empty.")
                else:
                    self.create_symlinks(category, src_name, tgt_name)
                    src_entry.set_text("")
                    tgt_entry.set_text("")
        dialog.destroy()

    def create_symlinks(self, category: str, src_name: str, tgt_name: str):
        if not self.theme_path:
            self.show_message("Error", "No icon theme loaded.")
            return
        category_path = os.path.join(self.theme_path, category)
        if not os.path.isdir(category_path):
            self.show_message("Error", f"Category folder '{category}' not found in theme.")
            return
        created_any = False
        errors = []
        available_sizes = [d for d in os.listdir(category_path) if d.isdigit() and os.path.isdir(os.path.join(category_path, d))]
        for size in available_sizes:
            size_dir = os.path.join(category_path, size)
            src_svg = os.path.join(size_dir, src_name + ".svg")
            tgt_svg = os.path.join(size_dir, tgt_name + ".svg")
            if os.path.exists(src_svg):
                try:
                    os.makedirs(os.path.dirname(tgt_svg), exist_ok=True)
                    if os.path.lexists(tgt_svg):
                        os.remove(tgt_svg)
                    rel_path = os.path.relpath(src_svg, os.path.dirname(tgt_svg))
                    os.symlink(rel_path, tgt_svg)
                    created_any = True
                except Exception as e:
                    errors.append(f"Error creating symlink {tgt_svg}: {e}")
            src_png = os.path.join(size_dir, src_name + ".png")
            tgt_png = os.path.join(size_dir, tgt_name + ".png")
            if os.path.exists(src_png):
                try:
                    os.makedirs(os.path.dirname(tgt_png), exist_ok=True)
                    if os.path.lexists(tgt_png):
                        os.remove(tgt_png)
                    rel_path = os.path.relpath(src_png, os.path.dirname(tgt_png))
                    os.symlink(rel_path, tgt_png)
                    created_any = True
                except Exception as e:
                    errors.append(f"Error creating symlink {tgt_png}: {e}")
        if created_any:
            clear_pixbuf_cache()
            self.show_message("Success", f"Symlinks created for target '{tgt_name}' in category '{category}'.")
        else:
            self.show_message("Warning", "No source files found to create symlinks.")
        if errors:
            self.show_message("Errors", "\n".join(errors))

    # ----------------------------------------------------------------------
    # Metadata editor (unchanged aside from backup hook)
    # ----------------------------------------------------------------------
    def show_svg_metadata_dialog(self, svg_path):
        try:
            tree = ET.parse(svg_path)
        except Exception as e:
            self.show_message("Error", f"Failed to parse SVG: {e}")
            return
        root = tree.getroot()
        ns = {'svg': 'http://www.w3.org/2000/svg', 'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
              'cc': 'http://creativecommons.org/ns#', 'dc': 'http://purl.org/dc/elements/1.1/'}
        metadata = root.find('svg:metadata', ns)
        fields = {"license": "", "author": "", "title": "", "date": "", "contributor": "", "description": ""}
        if metadata is not None:
            rdf = metadata.find('rdf:RDF', ns)
            if rdf is not None:
                work = rdf.find('cc:Work', ns)
                if work is not None:
                    lic = work.find('cc:license', ns)
                    if lic is not None:
                        fields["license"] = lic.attrib.get('{%s}resource' % ns['rdf'], "")
                    creator = work.find('dc:creator', ns)
                    if creator is not None:
                        agent = creator.find('cc:Agent', ns)
                        if agent is not None:
                            title = agent.find('dc:title', ns)
                            if title is not None:
                                fields["author"] = title.text or ""
                    title = work.find('dc:title', ns)
                    if title is not None:
                        fields["title"] = title.text or ""
                    date = work.find('dc:date', ns)
                    if date is not None:
                        fields["date"] = date.text or ""
                    contributor = work.find('dc:contributor', ns)
                    if contributor is not None:
                        agent = contributor.find('cc:Agent', ns)
                        if agent is not None:
                            title = agent.find('dc:title', ns)
                            if title is not None:
                                fields["contributor"] = title.text or ""
                    desc = work.find('dc:description', ns)
                    if desc is not None:
                        fields["description"] = desc.text or ""
        dialog = Gtk.Dialog(title="Edit SVG Metadata", transient_for=self, flags=0)
        dialog.set_default_size(500, 600)
        dialog.set_resizable(False)
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=10, row_spacing=10, margin=10)
        grid.set_hexpand(True)
        grid.set_vexpand(True)
        dialog.get_content_area().add(grid)
        license_label = Gtk.Label(label="License:", halign=Gtk.Align.START)
        license_label.set_hexpand(False)
        grid.attach(license_label, 0, 0, 1, 1)
        license_combo = Gtk.ComboBoxText()
        license_combo.set_hexpand(True)
        license_urls = {"CC0": "http://creativecommons.org/publicdomain/zero/1.0/",
                        "GPLv3": "https://www.gnu.org/licenses/gpl-3.0.en.html",
                        "MIT": "https://opensource.org/licenses/MIT", "Custom": ""}
        for name in license_urls:
            license_combo.append_text(name)
        lic_text = fields["license"]
        if lic_text in license_urls.values():
            lic_idx = list(license_urls.values()).index(lic_text)
            license_combo.set_active(lic_idx)
        else:
            license_combo.set_active(0)
        grid.attach(license_combo, 1, 0, 1, 1)
        license_entry = Gtk.Entry()
        license_entry.set_text(lic_text)
        license_entry.set_hexpand(True)
        grid.attach(license_entry, 1, 1, 1, 1)
        license_entry.set_placeholder_text("License URL (for 'Custom')")
        def add_row(label_text, value, row):
            label = Gtk.Label(label=label_text, halign=Gtk.Align.START)
            label.set_hexpand(False)
            entry = Gtk.Entry()
            entry.set_text(value)
            entry.set_hexpand(True)
            grid.attach(label, 0, row, 1, 1)
            grid.attach(entry, 1, row, 1, 1)
            return entry
        author_entry = add_row("Author:", fields["author"], 2)
        title_entry = add_row("Title:", fields["title"], 3)
        date_entry = add_row("Date:", fields["date"], 4)
        contributor_entry = add_row("Contributor:", fields["contributor"], 5)
        desc_entry = add_row("Description:", fields["description"], 6)
        def on_license_combo_changed(combo):
            text = combo.get_active_text()
            if text and text in license_urls and license_urls[text]:
                license_entry.set_text(license_urls[text])
            elif text == "Custom":
                license_entry.set_text("")
        license_combo.connect("changed", on_license_combo_changed)
        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            new_fields = {"license": license_entry.get_text(), "author": author_entry.get_text(),
                          "title": title_entry.get_text(), "date": date_entry.get_text() or datetime.date.today().isoformat(),
                          "contributor": contributor_entry.get_text(), "description": desc_entry.get_text()}
            # backup before writing
            try:
                self.backup_svg(svg_path)
            except Exception:
                pass
            self.write_svg_metadata(svg_path, new_fields)
        dialog.destroy()

    def write_svg_metadata(self, svg_path, fields):
        import re
        with open(svg_path, "r", encoding="utf-8") as f:
            svg_text = f.read()
        svg_text = re.sub(r"<metadata[\s\S]*?</metadata>\n?", "", svg_text, flags=re.IGNORECASE)
        metadata_block = f'''  <metadata
        id="metadata2">
        <rdf:RDF>
        <cc:Work
            rdf:about="">
            <dc:format>image/svg+xml</dc:format>
            <dc:type
            rdf:resource="http://purl.org/dc/dcmitype/StillImage" />
            <cc:license
            rdf:resource="{fields.get("license", "")}" />
            <dc:creator>
            <cc:Agent>
                <dc:title>{fields.get("author", "")}</dc:title>
            </cc:Agent>
            </dc:creator>
            <dc:title>{fields.get("title", "")}</dc:title>
            <dc:date>{fields.get("date", "")}</dc:date>
            <dc:description>{fields.get("description", "")}</dc:description>
            <dc:contributor>
            <cc:Agent>
                <dc:title>{fields.get("contributor", "")}</dc:title>
            </cc:Agent>
            </dc:contributor>
        </cc:Work>
        </rdf:RDF>
    </metadata>
    '''
        svg_opening_match = re.search(r"<svg[^>]*>", svg_text, flags=re.IGNORECASE)
        if svg_opening_match:
            insert_pos = svg_opening_match.end()
            svg_text = svg_text[:insert_pos] + "\n" + metadata_block + svg_text[insert_pos:]
        else:
            svg_text = metadata_block + "\n" + svg_text
        # atomic write
        tmp = svg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(svg_text)
        os.replace(tmp, svg_path)

    # ----------------------------------------------------------------------
    # UI Controls
    # ----------------------------------------------------------------------
    def on_refresh_clicked(self, button):
        close_all_previews()
        clear_pixbuf_cache()
        if self.current_category:
            self.load_icons(self.current_category)

    def on_symlink_filter_toggled(self, checkbox):
        self.show_symlinks = checkbox.get_active()
        if self.current_category:
            self.load_icons(self.current_category)

    def on_symbolic_filter_toggled(self, checkbox):
        self.show_symbolic = checkbox.get_active()
        if self.current_category:
            self.load_icons(self.current_category)

    # ----------------------------------------------------------------------
    # Backups / Versions management
    # ----------------------------------------------------------------------
    def _backup_base_dir(self) -> Optional[str]:
        if not self.theme_path:
            return None
        return os.path.join(self.theme_path, ".iconhelper_trash")

    def get_backup_dir(self, icon_name: str, category: Optional[str] = None) -> Optional[str]:
        base = self._backup_base_dir()
        if not base:
            return None
        if not category:
            category = self.current_category or "unknown"
        return os.path.join(base, category, icon_name)

    def list_backups(self, icon_name: str, category: Optional[str] = None) -> List[str]:
        """
        Return a list of backup file paths (absolute) for the given icon name.
        Tries multiple matching strategies:
        - exact match against the current indexed source path (preferred)
        - match by recorded icon_name in the backup metadata
        - fallback: match where the backup's source_path basename == icon_name + '.svg'
        Results are sorted newest-first.
        """
        out = []
        try:
            idx = _load_backup_index()
            if not isinstance(idx, dict):
                return []
            # preferred exact source path from current index
            source_path = self.icon_index.get(icon_name)
            for uid, meta in idx.items():
                if not isinstance(meta, dict):
                    continue
                meta_source = meta.get("source_path", "")
                # 1) exact source_path match (most reliable)
                if source_path and os.path.abspath(meta_source) == os.path.abspath(source_path):
                    out.append(os.path.join(BACKUP_FILES_DIR, meta.get("fname", "")))
                    continue
                # 2) recorded icon_name match
                if meta.get("icon_name") == icon_name:
                    # if category provided, prefer matching category too
                    if category:
                        if meta.get("category") == category:
                            out.append(os.path.join(BACKUP_FILES_DIR, meta.get("fname", "")))
                    else:
                        out.append(os.path.join(BACKUP_FILES_DIR, meta.get("fname", "")))
                    continue
                # 3) fallback: source_path basename equals icon_name.svg
                if meta_source and os.path.basename(meta_source) == icon_name + ".svg":
                    out.append(os.path.join(BACKUP_FILES_DIR, meta.get("fname", "")))
                    continue
            # Keep only existing files and sort newest-first
            out = [p for p in out if os.path.exists(p)]
            out.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
        except Exception as e:
            print(f"Failed to list backups for {icon_name}: {e}")
        return out

    def backup_svg(self, svg_path: str, icon_name: Optional[str] = None, category: Optional[str] = None) -> Optional[str]:
        """
        Create a backup copy of svg_path in SCRIPT_DIR/.iconhelper_backups/files and
        record metadata in index.json. Returns backup id (filename without ext) or None on error.
        """
        try:
            if not svg_path or not os.path.exists(svg_path):
                return None
            ensure_backup_dirs()
            idx = _load_backup_index()

            icon_name = icon_name or os.path.splitext(os.path.basename(svg_path))[0]
            category = category or self.current_category or ""

            ts = int(time.time())
            # unique id: short sha1 of path + ts
            uid = hashlib.sha1((os.path.abspath(svg_path) + str(ts)).encode("utf-8")).hexdigest()[:16]
            fname = f"{uid}.svg"
            dst = os.path.join(BACKUP_FILES_DIR, fname)

            # copy atomically
            tmp = dst + ".tmp"
            shutil.copy2(svg_path, tmp)
            os.replace(tmp, dst)

            meta = {
                "fname": fname,
                "source_path": os.path.abspath(svg_path),
                "icon_name": icon_name,
                "category": category,
                "created": ts
            }
            idx[uid] = meta
            _save_backup_index(idx)

            # prune older backups for this source (by created desc), keep MAX_SVG_BACKUPS
            entries = [(k, v) for k, v in idx.items() if v.get("source_path") == meta["source_path"]]
            entries.sort(key=lambda kv: kv[1].get("created", 0), reverse=True)
            for old_k, old_v in entries[MAX_SVG_BACKUPS:]:
                try:
                    old_file = os.path.join(BACKUP_FILES_DIR, old_v.get("fname", ""))
                    if os.path.exists(old_file):
                        os.remove(old_file)
                except Exception:
                    pass
                idx.pop(old_k, None)
            _save_backup_index(idx)
            return uid
        except Exception as e:
            print(f"Failed to backup svg {svg_path}: {e}")
            return None

    def restore_backup(self, backup_path: str):
        """
        Restore a backup (absolute path to backup file inside BACKUP_FILES_DIR) to its recorded source_path.
        """
        try:
            if not os.path.exists(backup_path):
                self.show_message("Error", "Backup file not found")
                return
            # find uid from filename
            fname = os.path.basename(backup_path)
            uid = os.path.splitext(fname)[0]
            idx = _load_backup_index()
            meta = idx.get(uid)
            if not meta:
                self.show_message("Error", "Backup metadata missing")
                return
            target = meta.get("source_path")
            if not target:
                self.show_message("Error", "Original file path not recorded; cannot restore automatically")
                return
            # ensure target dir exists
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = target + ".tmp"
            shutil.copy2(backup_path, tmp)
            os.replace(tmp, target)
            # regenerate bitmaps in background
            invalidate_pixbuf_cache_for_path(target)
            threading.Thread(target=self.watch_and_generate, args=(target,), daemon=True).start()
            self.show_message("Restored", f"Restored backup to {target}")
            if self.current_category:
                GLib.idle_add(self.load_icons, self.current_category)
        except Exception as e:
            print(f"Failed to restore backup {backup_path}: {e}")
            self.show_message("Error", f"Failed to restore backup: {e}")

    def delete_backup_file(self, backup_path: str):
        """
        Delete a backup file and remove its index entry.
        Accepts absolute path to backup file (in BACKUP_FILES_DIR).
        """
        try:
            if not os.path.exists(backup_path):
                return
            fname = os.path.basename(backup_path)
            uid = os.path.splitext(fname)[0]
            # remove file
            os.remove(backup_path)
            # remove index entry
            idx = _load_backup_index()
            if uid in idx:
                idx.pop(uid, None)
                _save_backup_index(idx)
        except Exception as e:
            print(f"Failed to delete backup {backup_path}: {e}")

    def show_versions_dialog(self, icon_name: str, category: Optional[str] = None):
        backups = self.list_backups(icon_name, category)
        if not backups:
            self.show_message("Versions", "No backups available for this icon.")
            return

        dialog = Gtk.Dialog(title=f"Versions for {icon_name}", transient_for=self, flags=0)
        dialog.add_buttons(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        dialog.set_default_size(800, 500)

        content = dialog.get_content_area()
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, margin=8)
        content.pack_start(hbox, True, True, 0)

        # Left: list of backups
        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        scrolled_left = Gtk.ScrolledWindow()
        scrolled_left.set_min_content_width(320)
        scrolled_left.add(listbox)
        hbox.pack_start(scrolled_left, False, False, 0)

        # Right: preview + actions
        right_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        preview_image = Gtk.Image()
        preview_frame = Gtk.Frame()
        preview_frame.set_shadow_type(Gtk.ShadowType.IN)
        preview_frame.add(preview_image)
        right_vbox.pack_start(preview_frame, True, True, 0)

        action_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        restore_btn = Gtk.Button(label="Restore selected")
        delete_btn = Gtk.Button(label="Delete selected")
        clear_btn = Gtk.Button(label="Clear all")
        action_hbox.pack_start(restore_btn, False, False, 0)
        action_hbox.pack_start(delete_btn, False, False, 0)
        action_hbox.pack_start(clear_btn, False, False, 0)
        right_vbox.pack_start(action_hbox, False, False, 0)

        hbox.pack_start(right_vbox, True, True, 0)

        # populate listbox with backups (use mapping for robust lookup)
        rows: List[Tuple[Gtk.ListBoxRow, str]] = []
        row_to_path: Dict[Gtk.ListBoxRow, str] = {}
        idx = _load_backup_index()
        for bp in backups:
            uid = os.path.splitext(os.path.basename(bp))[0]
            meta = idx.get(uid, {}) if isinstance(idx, dict) else {}
            label_text = os.path.basename(bp)
            row = Gtk.ListBoxRow()
            h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            lbl = Gtk.Label(label=label_text, xalign=0)
            ts = meta.get("created")
            if ts:
                try:
                    lbl.set_tooltip_text(datetime.datetime.fromtimestamp(ts).isoformat())
                except Exception:
                    pass
            h.pack_start(lbl, True, True, 0)
            row.add(h)
            listbox.add(row)
            rows.append((row, bp))
            row_to_path[row] = bp

        def _on_sel_changed(lb, sel):
            # show placeholder while loading or if nothing selected
            preview_image.set_from_pixbuf(get_or_load_pixbuf_sync(PLACEHOLDER_PATH, 256))
            if not sel:
                return
            bp = row_to_path.get(sel)
            if not bp:
                return

            # Enqueue async load, but verify selection hasn't changed before setting image.
            expected = bp

            def _set(pb, expected_path=expected):
                cur = listbox.get_selected_row()
                cur_path = row_to_path.get(cur) if cur else None
                if cur_path == expected_path:
                    try:
                        preview_image.set_from_pixbuf(pb)
                    except Exception:
                        pass
                # otherwise discard this callback silently (a newer selection took place)

            enqueue_pixbuf_load(bp, 256, _set)

        listbox.connect("row-selected", _on_sel_changed)

        dialog.show_all()

        # select the first row (if any) so the preview is shown immediately
        if rows:
            try:
                first_row = rows[0][0]
                listbox.select_row(first_row)
                # handler will enqueue preview load
            except Exception:
                pass

        # action handlers using the mapping
        def _restore_clicked(w):
            sel = listbox.get_selected_row()
            if not sel:
                self.show_message("Info", "No selection")
                return
            bp = row_to_path.get(sel)
            if bp:
                self.restore_backup(bp)

        def _delete_clicked(w):
            sel = listbox.get_selected_row()
            if not sel:
                self.show_message("Info", "No selection")
                return
            bp = row_to_path.get(sel)
            if not bp:
                return
            # remove from disk and index
            self.delete_backup_file(bp)
            # remove row widget
            try:
                listbox.remove(sel)
            except Exception:
                pass
            # update local mappings
            rows[:] = [(r, p) for (r, p) in rows if r is not sel]
            row_to_path.pop(sel, None)
            preview_image.set_from_pixbuf(get_or_load_pixbuf_sync(PLACEHOLDER_PATH, 256))

        def _clear_all(w):
            confirm = Gtk.MessageDialog(transient_for=self, flags=0,
                                        message_type=Gtk.MessageType.QUESTION,
                                        buttons=Gtk.ButtonsType.YES_NO,
                                        text=f"Delete all backups for {icon_name}?")
            resp = confirm.run()
            confirm.destroy()
            if resp == Gtk.ResponseType.YES:
                # iterate a snapshot of paths
                for r, bp in list(rows):
                    try:
                        self.delete_backup_file(bp)
                    except Exception:
                        pass
                    try:
                        listbox.remove(r)
                    except Exception:
                        pass
                rows.clear()
                row_to_path.clear()
                preview_image.set_from_pixbuf(get_or_load_pixbuf_sync(PLACEHOLDER_PATH, 256))

        restore_btn.connect("clicked", _restore_clicked)
        delete_btn.connect("clicked", _delete_clicked)
        clear_btn.connect("clicked", _clear_all)

        dialog.run()
        dialog.destroy()

    # ----------------------------------------------------------------------
    # Delete icons function
    # ----------------------------------------------------------------------
    def delete_icon_files(self, icon_name, remove_from_json=False):
        if not self.theme_path or not self.current_category:
            self.show_message("Error", "Theme or category not selected")
            return
        category_path = os.path.join(self.theme_path, self.current_category)
        if not os.path.isdir(category_path):
            self.show_message("Error", f"Category folder '{self.current_category}' not found in theme.")
            return
        deleted = []
        for size in os.listdir(category_path):
            size_dir = os.path.join(category_path, size)
            if not os.path.isdir(size_dir):
                continue
            for ext in (".svg", ".png"):
                file_path = os.path.join(size_dir, icon_name + ext)
                if os.path.lexists(file_path):
                    try:
                        os.remove(file_path)
                        print(f"Deleted: {file_path}")
                        deleted.append(file_path)
                        invalidate_pixbuf_cache_for_path(file_path)
                    except Exception as e:
                        print(f"Failed to delete {file_path}: {e}")
        if deleted:
            self.show_message("Deleted", f"Deleted {len(deleted)} files for icon '{icon_name}'.")
        else:
            print(f"No files deleted for icon: {icon_name}")
            self.show_message("Info", f"No files found to delete for icon '{icon_name}'.")
        if remove_from_json:
            icons = self.icon_categories.get(self.current_category, [])
            if icon_name in icons:
                icons.remove(icon_name)
                try:
                    with open(CATEGORIES_FILE, "w", encoding="utf-8") as f:
                        json.dump(self.icon_categories, f, indent=2)
                    print(f"Removed {icon_name} from JSON for category {self.current_category}")
                except Exception as e:
                    print(f"Failed to update JSON: {e}")
        if self.current_category:
            self.load_icons(self.current_category)

    # ----------------------------------------------------------------------
    # Install theme to ~/.icons
    # ----------------------------------------------------------------------
    def on_install_theme_clicked(self, button):
        if not self.theme_path:
            self.show_message("Error", "No theme selected")
            return
        target_root = os.path.expanduser("~/.icons")
        os.makedirs(target_root, exist_ok=True)
        theme_name = os.path.basename(os.path.normpath(self.theme_path))
        target = os.path.join(target_root, theme_name)
        if os.path.exists(target):
            dialog = Gtk.MessageDialog(transient_for=self, flags=0, message_type=Gtk.MessageType.QUESTION,
                                       buttons=Gtk.ButtonsType.YES_NO, text=f"Overwrite existing theme at {target}?")
            dialog.format_secondary_text("This will replace the installed theme.")
            resp = dialog.run()
            dialog.destroy()
            if resp != Gtk.ResponseType.YES:
                return
            # remove existing
            try:
                shutil.rmtree(target)
            except Exception as e:
                self.show_message("Error", f"Failed to remove existing target: {e}")
                return
        # Copy into a temporary directory then move atomically
        tmpdir = None
        try:
            tmpdir = tempfile.mkdtemp(prefix="iconhelper_install_")
            dst_tmp = os.path.join(tmpdir, theme_name)
            shutil.copytree(self.theme_path, dst_tmp)
            # move into place
            os.replace(dst_tmp, target)
            # attempt to run gtk-update-icon-cache if available
            gtk_update = shutil.which("gtk-update-icon-cache")
            if gtk_update:
                try:
                    subprocess.run([gtk_update, "-f", target], check=False)
                except Exception:
                    pass
            self.show_message("Installed", f"Theme installed to {target}")
        except Exception as e:
            self.show_message("Error", f"Failed to install theme: {e}")
        finally:
            if tmpdir and os.path.isdir(tmpdir):
                try:
                    shutil.rmtree(tmpdir)
                except Exception:
                    pass

# --------------------------------------------------------------------------
# Helper synchronous loader for initial placeholders
# --------------------------------------------------------------------------

def get_or_load_pixbuf_sync(path: str, size: int) -> Optional[GdkPixbuf.Pixbuf]:
    if not path or not os.path.exists(path):
        path = PLACEHOLDER_PATH
    key = (path, size)
    pix = _cache_get(key)
    if pix:
        return pix
    disk_pb = load_disk_cache(path, size)
    if disk_pb:
        _cache_set(key, disk_pb)
        return disk_pb
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_size(path, size, size)
    except Exception:
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_size(PLACEHOLDER_PATH, size, size)
            path = PLACEHOLDER_PATH
        except Exception:
            pb = None
    if pb:
        _cache_set(key, pb)
        try:
            store_disk_cache(path, size, pb)
        except Exception:
            pass
    return pb

# --------------------------------------------------------------------------
# Application entry point
# --------------------------------------------------------------------------

if __name__ == "__main__":
    if DISK_CACHE_ENABLED:
        ensure_disk_cache_dir()
    win = IconThemeHelper()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()

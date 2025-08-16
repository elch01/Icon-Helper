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
from typing import Callable, Dict, List, Optional
from gi.repository import Gtk, GdkPixbuf, GLib, Gdk


# --------------------------------------------------------------------------
# Globals and Constants
# --------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

PLACEHOLDER_PATH = os.path.join(SCRIPT_DIR, "emblem-unreadable.svg")
SYMLINK_EMBLEM_PATH = os.path.join(SCRIPT_DIR, "emblem-symlink.png")
TEMPLATE_SVG = os.path.join(SCRIPT_DIR, "template.svg")
BITMAP_SIZES = [16, 22, 24, 32, 48]
CATEGORIES_FILE = os.path.join(SCRIPT_DIR, "icon_categories.json")
PNG_EMBLEM = os.path.join(SCRIPT_DIR, "emblem-png.png")


# --------------------------------------------------------------------------
# Utility Functions
# --------------------------------------------------------------------------

def check_file_exists(path: str) -> bool:
    """Utility to check if a file exists and show an error if not."""
    if not os.path.isfile(path):
        print(f"Required file missing: {path}")
        return False
    return True


# --------------------------------------------------------------------------
# LazyIconBox Widget
# --------------------------------------------------------------------------

class LazyIconBox(Gtk.EventBox):
    """Widget to display an icon preview with overlays and label."""

    def __init__(self, icon_name: str, icon_path: str, click_cb: Callable):
        super().__init__()
        self.icon_name = icon_name
        self.icon_path = icon_path
        self.click_cb = click_cb

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add(vbox)

        self.overlay = Gtk.Overlay()
        vbox.pack_start(self.overlay, False, False, 0)

        self.image = Gtk.Image()
        self.overlay.add(self.image)

        label = Gtk.Label(label=icon_name)
        label.set_ellipsize(True)
        label.set_max_width_chars(15)
        vbox.pack_start(label, False, False, 0)

        self.connect("button-press-event", self.on_button_press)

        self.update_icon(icon_path)

        # --- Hover-to-enlarge logic ---
        self.hover_timeout_id = None
        self.popup = None
        self.connect("enter-notify-event", self.on_mouse_enter)
        self.connect("leave-notify-event", self.on_mouse_leave)

    def on_mouse_enter(self, widget, event):
        if self.hover_timeout_id is None:
            self.hover_timeout_id = GLib.timeout_add(2000, self.show_enlarged_preview)
        return True

    def on_mouse_leave(self, widget, event):
        # Only remove if it exists
        if self.hover_timeout_id is not None:
            try:
                GLib.source_remove(self.hover_timeout_id)
            except Exception:
                pass
            self.hover_timeout_id = None
        self.hide_enlarged_preview()
        return True

    def show_enlarged_preview(self):
        if self.popup:
            self.popup.destroy()
            self.popup = None

        self.popup = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.popup.set_decorated(False)
        self.popup.set_border_width(8)
        self.popup.set_resizable(False)

        # Load larger icon
        pixbuf = None
        if self.icon_path and check_file_exists(self.icon_path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(self.icon_path, 512, 512)
            except Exception as e:
                print(f"Error loading enlarged icon: {e}")

        if pixbuf is None:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(PLACEHOLDER_PATH, 512, 512)

        image = Gtk.Image.new_from_pixbuf(pixbuf)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.pack_start(image, True, True, 0)
        label = Gtk.Label(label=self.icon_name)
        box.pack_start(label, False, False, 0)
        self.popup.add(box)
        self.popup.show_all()

        # Position the popup near the icon
        display = Gdk.Display.get_default()
        pointer = display.get_default_seat().get_pointer()
        screen, x, y = pointer.get_position()
        self.popup.move(x + 16, y + 16)

        # Reset the timeout id so it doesn't repeat
        self.hover_timeout_id = None
        return False

    def hide_enlarged_preview(self):
        if self.popup:
            self.popup.destroy()
            self.popup = None

    def update_icon(self, icon_path: str):
        """Update icon preview and overlays."""

        # Load main icon
        pixbuf = None
        if icon_path and icon_path != PLACEHOLDER_PATH and check_file_exists(icon_path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, 64, 64)
            except Exception as e:
                print(f"Error loading icon '{icon_path}': {e}")

        if pixbuf is None:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(PLACEHOLDER_PATH, 64, 64)
            self.icon_path = PLACEHOLDER_PATH

        self.image.set_from_pixbuf(pixbuf)

        # Remove previous overlays
        for attr in ('emblem', 'png_emblem'):
            if hasattr(self, attr):
                self.overlay.remove(getattr(self, attr))
                delattr(self, attr)

        # Add overlays
        if icon_path != PLACEHOLDER_PATH and os.path.islink(icon_path):
            if check_file_exists(SYMLINK_EMBLEM_PATH):
                emblem_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(SYMLINK_EMBLEM_PATH, 16, 16)
                self.emblem = Gtk.Image.new_from_pixbuf(emblem_pixbuf)
                self.emblem.set_halign(Gtk.Align.END)
                self.emblem.set_valign(Gtk.Align.START)
                self.overlay.add_overlay(self.emblem)
                self.emblem.show()

        elif icon_path != PLACEHOLDER_PATH and icon_path.lower().endswith('.png'):
            if check_file_exists(PNG_EMBLEM):
                png_emblem_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(PNG_EMBLEM, 16, 16)
                self.png_emblem = Gtk.Image.new_from_pixbuf(png_emblem_pixbuf)
                self.png_emblem.set_halign(Gtk.Align.END)
                self.png_emblem.set_valign(Gtk.Align.START)
                self.overlay.add_overlay(self.png_emblem)
                self.png_emblem.show()

                for attr in ('warning_overlay',):
                    if hasattr(self, attr):
                        self.overlay.remove(getattr(self, attr))
                        delattr(self, attr)
                        
        if icon_path.lower().endswith(".svg") and check_file_exists(icon_path):
            try:
                size_bytes = os.path.getsize(icon_path)
                if size_bytes > 1024 * 1024:  # > 1MB
                    warning_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(
                        os.path.join(SCRIPT_DIR, "warning-triangle.svg"), 20, 20)
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
            except Exception as e:
                print("Validation error:", e)

    def on_button_press(self, widget, event):
        # Always allow right-click, even for missing icons
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
        # Add the "permanently remove" checkbox
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
        is_svg = (
            not is_missing
            and self.icon_path.lower().endswith(".svg")
            and not os.path.islink(self.icon_path)
        )

        # Edit Metadata only for SVGs that are not symlinks
        if is_svg:
            edit_item = Gtk.MenuItem(label="Edit Metadata")
            edit_item.connect("activate", self.edit_metadata)
            menu.append(edit_item)
        
        # "Clear Existing Icon" for any present icon
        if not is_missing:
            clear_item = Gtk.MenuItem(label="Clear Existing Icon")
            clear_item.connect("activate", self.clear_icon)
            menu.append(clear_item)

        # "Permanently Remove" always available
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
    """Main application window for icon theme management."""



    def __init__(self):
        super().__init__(title="Icon Theme Helper")
        self.set_default_size(1200, 800)

        self.icon_categories: Dict[str, List[str]] = {}
        self.theme_path: Optional[str] = None
        self.current_category: Optional[str] = None
        self.icon_index: Dict[str, str] = {}
        self.icon_boxes: List[LazyIconBox] = []
        self.indexing_done: bool = False

        # Set window icon
        icon_path = os.path.join(SCRIPT_DIR, 'icon-helper-logo.svg')
        if os.path.exists(icon_path):
            self.set_icon_from_file(icon_path)

        # Search text for filtering icons
        self.search_text: str = ""

        if not check_file_exists(CATEGORIES_FILE):
            self.show_message("Error", f"Missing categories file: {CATEGORIES_FILE}")
            return

        try:
            with open(CATEGORIES_FILE, "r") as f:
                self.icon_categories = json.load(f)
        except Exception as e:
            self.show_message("Error", f"Cannot load categories: {e}")
            return

        self.setup_ui()

    def setup_ui(self):
        """Initialize main UI widgets."""

        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add(main_box)

        # Sidebar
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        self.category_list = Gtk.ListBox()
        self.category_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.category_list.connect("row-selected", self.on_category_selected)
        self.category_list.set_sensitive(False)

        for category in self.icon_categories.keys():
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=category, xalign=0)
            row.add(label)
            self.category_list.add(row)

        choose_btn = Gtk.Button(label="Choose Theme Folder")
        choose_btn.connect("clicked", self.on_choose_theme)

        refresh_btn = Gtk.Button(label="Refresh Icons")
        refresh_btn.connect("clicked", self.on_refresh_clicked)
        sidebar_box.pack_start(refresh_btn, False, False, 0)
        sidebar_box.pack_start(choose_btn, False, False, 0)
        sidebar_box.pack_start(self.category_list, True, True, 0)

        # --- Place the search bar above the checkboxes in the sidebar ---
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        search_label = Gtk.Label(label="Search:", xalign=0)
        self.search_entry = Gtk.Entry()
        self.search_entry.set_placeholder_text("Filter icons by name...")
        self.search_entry.connect("changed", self.on_search_changed)
        search_box.pack_start(search_label, False, False, 0)
        search_box.pack_start(self.search_entry, True, True, 0)
        sidebar_box.pack_start(search_box, False, False, 0)  # <-- changed position

        self.status_filter_combo = Gtk.ComboBoxText()
        self.status_filter_combo.append_text("All Icons")
        self.status_filter_combo.append_text("All Except Symlinks")
        self.status_filter_combo.append_text("Missing Icons")
        self.status_filter_combo.append_text("Only SVG")
        self.status_filter_combo.append_text("Only PNG")
        self.status_filter_combo.append_text("Symlinks Only")
        self.status_filter_combo.append_text("Large Files")
        self.status_filter_combo.set_active(0)
        self.status_filter_combo.connect("changed", self.on_status_filter_changed)
        sidebar_box.pack_start(self.status_filter_combo, False, False, 0)
        self.current_status_filter = "All Icons"

        self.create_symlink_btn = Gtk.Button(label="Create Symlink")
        self.create_symlink_btn.set_sensitive(False)
        self.create_symlink_btn.connect("clicked", self.on_create_symlink_clicked)
        sidebar_box.pack_start(self.create_symlink_btn, False, False, 0)

        main_box.pack_start(sidebar_box, False, False, 0)

        # Icon grid
        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.set_max_children_per_line(10)
        self.flowbox.set_selection_mode(Gtk.SelectionMode.NONE)

        scrolled = Gtk.ScrolledWindow()
        scrolled.add(self.flowbox)
        main_box.pack_start(scrolled, True, True, 0)

        self.show_symlinks = False
        self.show_symbolic = False

    # ----------------------------------------------------------------------
    # Dialogs and Messaging
    # ----------------------------------------------------------------------

    def show_message(self, title: str, message: str):
        """Show user a modal message dialog."""
        md = Gtk.MessageDialog(parent=self, flags=0, message_type=Gtk.MessageType.INFO,
                               buttons=Gtk.ButtonsType.OK, text=title)
        md.format_secondary_text(message)
        md.run()
        md.destroy()

    # ----------------------------------------------------------------------
    # Theme Folder Indexing and Loading
    # ----------------------------------------------------------------------

    def on_choose_theme(self, widget):
        """Let user select icon theme directory."""
        dialog = Gtk.FileChooserDialog(
            title="Select Icon Theme Folder",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
            buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                     Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        )

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            self.theme_path = dialog.get_filename()
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
                        # Try to get size from directory name
                        parent_dir = os.path.basename(root)
                        try:
                            size = int(parent_dir)
                        except ValueError:
                            size = 0
                        icons_found[icon_name]['pngs'].append((size, full_path))

        # Build final icon_index preferring SVG, otherwise largest PNG
        idx = {}
        for icon_name, sources in icons_found.items():
            if sources['svg']:
                idx[icon_name] = sources['svg']
            elif sources['pngs']:
                # pick largest size
                largest_png = max(sources['pngs'], key=lambda t: t[0])
                idx[icon_name] = largest_png[1]

        GLib.idle_add(self.update_icon_index, idx)

    def update_icon_index(self, idx: Dict[str, str]):
        """Update icon index and enable category selection."""
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

    def refresh_icon(self, changed_filename: str):
        """Reload icons after changes."""
        if self.current_category:
            self.load_icons(self.current_category)
        return False


    # ----------------------------------------------------------------------
    # Category and Icon Display
    # ----------------------------------------------------------------------

    def on_category_selected(self, listbox, row):
        """Handle category selection."""
        if not row or not self.indexing_done:
            return

        category_name = row.get_child().get_text()
        self.current_category = category_name
        self.load_icons(category_name)

    def on_search_changed(self, entry):
        """Handle search bar text change to filter icons."""
        self.search_text = entry.get_text().strip().lower()
        if self.current_category:
            self.load_icons(self.current_category)

    def load_icons(self, category_name: str):
        self.flowbox.foreach(lambda child: self.flowbox.remove(child))
        self.icon_boxes.clear()

        icon_names = self.icon_categories.get(category_name, [])
        # Filter by search
        if self.search_text:
            icon_names = [name for name in icon_names if self.search_text in name.lower()]

        filtered_names = []
        for icon_name in icon_names:
            icon_path = self.icon_index.get(icon_name)
            if self.current_status_filter == "All Icons":
                filtered_names.append(icon_name)
            elif self.current_status_filter == "Missing Icons":
                if not icon_path or not os.path.isfile(icon_path):
                    filtered_names.append(icon_name)
            elif self.current_status_filter == "Only SVG":
                if icon_path and icon_path.lower().endswith(".svg"):
                    filtered_names.append(icon_name)
            elif self.current_status_filter == "Only PNG":
                if icon_path and icon_path.lower().endswith(".png"):
                    filtered_names.append(icon_name)
            elif self.current_status_filter == "Symlinks Only":
                if icon_path and os.path.islink(icon_path):
                    filtered_names.append(icon_name)
            elif self.current_status_filter == "All Except Symlinks":
                if not icon_path or not os.path.islink(icon_path):
                    filtered_names.append(icon_name)
            elif self.current_status_filter == "Large Files":
                if icon_path and (
                    (icon_path.lower().endswith(".svg") and os.path.getsize(icon_path) > 1024 * 1024) or
                    (icon_path.lower().endswith(".png") and os.path.getsize(icon_path) > 1024 * 1024)
                ):
                    filtered_names.append(icon_name)

        for icon_name in filtered_names:
            icon_path = self.icon_index.get(icon_name, PLACEHOLDER_PATH)
            box = LazyIconBox(icon_name, icon_path, self.on_icon_clicked)
            box.icon_helper = self
            self.flowbox.add(box)
            self.icon_boxes.append(box)

        self.show_all()


    def on_status_filter_changed(self, combo):
        self.current_status_filter = combo.get_active_text()
        if self.current_category:
            self.load_icons(self.current_category)

    # ----------------------------------------------------------------------
    # Icon Editing and Bitmap Generation
    # ----------------------------------------------------------------------

    def on_icon_clicked(self, icon_path: str, icon_name: str):
        """Handle icon click: create/edit SVG and watch for changes."""
        if not self.theme_path or not self.current_category:
            self.show_message("Error", "Theme or category not selected")
            return

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

        # Open SVG in Inkscape
        try:
            subprocess.Popen(["inkscape", new_icon_path])
        except Exception as e:
            self.show_message("Error", f"Failed to launch Inkscape: {e}")
            return

        threading.Thread(target=self.watch_and_generate, args=(new_icon_path,), daemon=True).start()

    def watch_and_generate(self, svg_path: str):
        """Watch SVG for changes and generate bitmaps when modified."""
        try:
            last_mtime = os.path.getmtime(svg_path)
        except Exception:
            return

        while True:
            time.sleep(2)
            try:
                mtime = os.path.getmtime(svg_path)
            except FileNotFoundError:
                print(f"Icon file deleted: {svg_path}")
                break

            if mtime != last_mtime:
                last_mtime = mtime
                self.generate_bitmaps(svg_path)
                GLib.idle_add(self.refresh_icon, os.path.basename(svg_path))
                break

    def generate_bitmaps(self, svg_path: str):
        """Export SVG to PNGs of various sizes."""
        base_name = os.path.splitext(os.path.basename(svg_path))[0]
        category = next(
            (cat for cat, icons in self.icon_categories.items() if base_name in icons),
            "fallback"
        )

        for size in BITMAP_SIZES:
            out_dir = os.path.join(self.theme_path, category, str(size))
            os.makedirs(out_dir, exist_ok=True)
            out_png = os.path.join(out_dir, base_name + ".png")

            cmd = [
                "inkscape",
                svg_path,
                f"--export-filename={out_png}",
                f"--export-width={size}",
                f"--export-height={size}",
            ]

            try:
                subprocess.run(cmd, check=True)
            except subprocess.CalledProcessError as e:
                print(f"Failed to export bitmap for size {size}: {e}")

        # Update icon index and refresh UI
        self.icon_index[base_name] = svg_path

        if self.current_category:
            GLib.idle_add(self.load_icons, self.current_category)

    # ----------------------------------------------------------------------
    # UI Controls
    # ----------------------------------------------------------------------

    def on_refresh_clicked(self, button):
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
    # Symlink Creation Dialog and Logic
    # ----------------------------------------------------------------------

    def on_create_symlink_clicked(self, button):
        """Show dialog to create symlinks for icons."""

        dialog = Gtk.Dialog(title="Create Symlink", transient_for=self, flags=0)
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK
        )
        dialog.set_default_size(400, 300)

        box = dialog.get_content_area()
        grid = Gtk.Grid(column_spacing=10, row_spacing=10, margin=10)
        box.add(grid)

        # Category dropdown
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

        # Source input
        src_label = Gtk.Label(label="Symlink Source (icon base name):", halign=Gtk.Align.START)
        grid.attach(src_label, 0, 1, 1, 1)
        src_entry = Gtk.Entry()
        src_entry.set_placeholder_text("e.g. cool-mimetype")
        grid.attach(src_entry, 1, 1, 1, 1)

        src_example = Gtk.Label(label="Example: 'cool-mimetype' (no extension)", halign=Gtk.Align.START)
        src_example.get_style_context().add_class("dim-label")
        grid.attach(src_example, 1, 2, 1, 1)

        # Target input
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
        """Create symlinks for SVG and PNG icons in theme."""

        if not self.theme_path:
            self.show_message("Error", "No icon theme loaded.")
            return

        category_path = os.path.join(self.theme_path, category)
        if not os.path.isdir(category_path):
            self.show_message("Error", f"Category folder '{category}' not found in theme.")
            return

        created_any = False
        errors = []

        available_sizes = [
            d for d in os.listdir(category_path)
            if d.isdigit() and os.path.isdir(os.path.join(category_path, d))
        ]

        for size in available_sizes:
            size_dir = os.path.join(category_path, size)

            # Try SVG first
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

            # Try PNG next
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
            self.show_message("Success", f"Symlinks created for target '{tgt_name}' in category '{category}'.")
        else:
            self.show_message("Warning", "No source files found to create symlinks.")

        if errors:
            self.show_message("Errors", "\n".join(errors))

# --------------------------------------------------------------------------
# Metadata editor
# --------------------------------------------------------------------------

    def show_svg_metadata_dialog(self, svg_path):
        # Parse existing metadata
        tree = ET.parse(svg_path)
        root = tree.getroot()
        ns = {
            'svg': 'http://www.w3.org/2000/svg',
            'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
            'cc': 'http://creativecommons.org/ns#',
            'dc': 'http://purl.org/dc/elements/1.1/'
        }
        metadata = root.find('svg:metadata', ns)
        fields = {
            "license": "",
            "author": "",
            "title": "",
            "date": "",
            "contributor": "",
            "description": ""
        }
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

        # Show dialog
        dialog = Gtk.Dialog(title="Edit SVG Metadata", transient_for=self, flags=0)
        dialog.set_default_size(500, 600)
        dialog.set_resizable(False) # feel free to enable it if needed
        dialog.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OK, Gtk.ResponseType.OK)
        grid = Gtk.Grid(column_spacing=10, row_spacing=10, margin=10)
        grid.set_hexpand(True)
        grid.set_vexpand(True)
        dialog.get_content_area().add(grid)

        # License dropdown and entry
        license_label = Gtk.Label(label="License:", halign=Gtk.Align.START)
        license_label.set_hexpand(False)
        grid.attach(license_label, 0, 0, 1, 1)
        license_combo = Gtk.ComboBoxText()
        license_combo.set_hexpand(True)
        license_urls = {
            "CC0": "http://creativecommons.org/publicdomain/zero/1.0/",
            "GPLv3": "https://www.gnu.org/licenses/gpl-3.0.en.html",
            "MIT": "https://opensource.org/licenses/MIT",
            "Custom": ""
        }
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

        # Author, title, date, contributor, description
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
            # Update fields
            new_fields = {
                "license": license_entry.get_text(),
                "author": author_entry.get_text(),
                "title": title_entry.get_text(),
                "date": date_entry.get_text() or datetime.date.today().isoformat(),
                "contributor": contributor_entry.get_text(),
                "description": desc_entry.get_text()
            }
            # Write back to SVG
            self.write_svg_metadata(svg_path, new_fields)
        dialog.destroy()

    def write_svg_metadata(self, svg_path, fields):
        import xml.etree.ElementTree as ET
        nsmap = {
            'svg': 'http://www.w3.org/2000/svg',
            'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
            'cc': 'http://creativecommons.org/ns#',
            'dc': 'http://purl.org/dc/elements/1.1/'
        }
        for prefix, uri in nsmap.items():
            ET.register_namespace(prefix, uri)
        tree = ET.parse(svg_path)
        root = tree.getroot()
        metadata = root.find('{%s}metadata' % nsmap['svg'])
        if metadata is not None:
            root.remove(metadata)
        metadata = ET.Element('{%s}metadata' % nsmap['svg'])
        rdf = ET.SubElement(metadata, '{%s}RDF' % nsmap['rdf'])
        work = ET.SubElement(rdf, '{%s}Work' % nsmap['cc'], attrib={'{%s}about' % nsmap['rdf']: ""})
        ET.SubElement(work, '{%s}format' % nsmap['dc']).text = "image/svg+xml"
        ET.SubElement(work, '{%s}type' % nsmap['dc'], attrib={'{%s}resource' % nsmap['rdf']: "http://purl.org/dc/dcmitype/StillImage"})
        ET.SubElement(work, '{%s}license' % nsmap['cc'], attrib={'{%s}resource' % nsmap['rdf']: fields["license"]})
        if fields["author"]:
            creator = ET.SubElement(work, '{%s}creator' % nsmap['dc'])
            agent = ET.SubElement(creator, '{%s}Agent' % nsmap['cc'])
            ET.SubElement(agent, '{%s}title' % nsmap['dc']).text = fields["author"]
        if fields["title"]:
            ET.SubElement(work, '{%s}title' % nsmap['dc']).text = fields["title"]
        if fields["date"]:
            ET.SubElement(work, '{%s}date' % nsmap['dc']).text = fields["date"]
        if fields["contributor"]:
            contributor = ET.SubElement(work, '{%s}contributor' % nsmap['dc'])
            agent = ET.SubElement(contributor, '{%s}Agent' % nsmap['cc'])
            ET.SubElement(agent, '{%s}title' % nsmap['dc']).text = fields["contributor"]
        if fields["description"]:
            ET.SubElement(work, '{%s}description' % nsmap['dc']).text = fields["description"]
        root.insert(0, metadata)
        tree.write(svg_path, encoding="utf-8", xml_declaration=True)

# --------------------------------------------------------------------------
# Delete icons function
# --------------------------------------------------------------------------

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
                        print(f"Deleted: {file_path}")  # Show path in terminal
                        deleted.append(file_path)
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
                # Save back to file
                try:
                    with open(CATEGORIES_FILE, "w") as f:
                        json.dump(self.icon_categories, f, indent=2)
                    print(f"Removed {icon_name} from JSON for category {self.current_category}")
                except Exception as e:
                    print(f"Failed to update JSON: {e}")

        if self.current_category:
            self.load_icons(self.current_category)

# --------------------------------------------------------------------------
# Application Entry Point
# --------------------------------------------------------------------------

if __name__ == "__main__":
    win = IconThemeHelper()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()

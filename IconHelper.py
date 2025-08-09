#!/usr/bin/env python3
import os
import json
import subprocess
import threading
import time
import shutil
# stop pointless version nagging
import gi
gi.require_version('Gtk', '3.0')

from gi.repository import Gtk, GdkPixbuf, GLib


# Main variables

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
PLACEHOLDER_PATH = os.path.join(SCRIPT_DIR, "emblem-unreadable.png")
SYMLINK_EMBLEM_PATH = os.path.join(SCRIPT_DIR, "emblem-symlink.png")
TEMPLATE_SVG = os.path.join(SCRIPT_DIR, "template.svg")
BITMAP_SIZES = [16, 22, 24, 32, 48]

class LazyIconBox(Gtk.EventBox):
    def __init__(self, icon_name, icon_path, click_cb):
        super().__init__()
        self.icon_name = icon_name
        self.icon_path = icon_path
        self.click_cb = click_cb

        # Container for image + label vertically
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add(vbox)

        # Overlay to stack icon and emblem
        self.overlay = Gtk.Overlay()
        vbox.pack_start(self.overlay, False, False, 0)

        self.image = Gtk.Image()
        self.overlay.add(self.image)

        label = Gtk.Label(label=icon_name)
        label.set_ellipsize(True)
        label.set_max_width_chars(15)
        vbox.pack_start(label, False, False, 0)

        self.connect("button-press-event", lambda w, e: self.click_cb(self.icon_path, self.icon_name))

        self.update_icon(icon_path)

    def update_icon(self, icon_path):
        if icon_path and icon_path != PLACEHOLDER_PATH:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(icon_path, 64, 64)
                self.image.set_from_pixbuf(pixbuf)
                self.icon_path = icon_path
            except Exception as e:
                print(f"Error loading icon '{icon_path}': {e}")
                placeholder_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(PLACEHOLDER_PATH, 64, 64)
                self.image.set_from_pixbuf(placeholder_pixbuf)
                self.icon_path = PLACEHOLDER_PATH
        else:
            placeholder_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(PLACEHOLDER_PATH, 64, 64)
            self.image.set_from_pixbuf(placeholder_pixbuf)
            self.icon_path = PLACEHOLDER_PATH

        # Remove any existing emblems before adding new ones
        if hasattr(self, 'emblem'):
            self.overlay.remove(self.emblem)
            del self.emblem
        if hasattr(self, 'png_emblem'):
            self.overlay.remove(self.png_emblem)
            del self.png_emblem

        # Add symlink emblem overlay if icon_path is symlink and not placeholder
        if icon_path != PLACEHOLDER_PATH and os.path.islink(icon_path):
            emblem_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(SYMLINK_EMBLEM_PATH, 16, 16)
            self.emblem = Gtk.Image.new_from_pixbuf(emblem_pixbuf)
            self.emblem.set_halign(Gtk.Align.END)
            self.emblem.set_valign(Gtk.Align.START)
            self.overlay.add_overlay(self.emblem)
            self.emblem.show()

        # Add PNG emblem overlay if the icon file is a PNG
        elif icon_path != PLACEHOLDER_PATH and icon_path.lower().endswith('.png'):
            png_emblem_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size('emblem-png.png', 16, 16)
            self.png_emblem = Gtk.Image.new_from_pixbuf(png_emblem_pixbuf)
            self.png_emblem.set_halign(Gtk.Align.END)
            self.png_emblem.set_valign(Gtk.Align.START)
            self.overlay.add_overlay(self.png_emblem)
            self.png_emblem.show()


class IconThemeHelper(Gtk.Window):
    def __init__(self):
        super().__init__(title="Icon Theme Helper")
        self.set_default_size(1500, 1000)

        with open("icon_categories.json", "r") as f:
            self.icon_categories = json.load(f)

        self.theme_path = None
        self.current_category = None
        self.icon_index = {}  # icon_name -> full_path
        self.icon_boxes = []  # keep refs to LazyIconBox for updating
        self.indexing_done = False

        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add(main_box)

        self.category_list = Gtk.ListBox()
        self.category_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.category_list.connect("row-selected", self.on_category_selected)
        self.category_list.set_sensitive(False)  # disabled until theme selected/indexed

        for category in self.icon_categories.keys():
            row = Gtk.ListBoxRow()
            label = Gtk.Label(label=category, xalign=0)
            row.add(label)
            self.category_list.add(row)

        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        choose_btn = Gtk.Button(label="Choose Theme Folder")
        choose_btn.connect("clicked", self.on_choose_theme)

        refresh_btn = Gtk.Button(label="Refresh Icons")
        refresh_btn.connect("clicked", self.on_refresh_clicked)
        sidebar_box.pack_start(refresh_btn, False, False, 0)

        sidebar_box.pack_start(choose_btn, False, False, 0)
        sidebar_box.pack_start(self.category_list, True, True, 0)

        main_box.pack_start(sidebar_box, False, False, 0)

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.set_max_children_per_line(10)
        self.flowbox.set_selection_mode(Gtk.SelectionMode.NONE)

        scrolled = Gtk.ScrolledWindow()
        scrolled.add(self.flowbox)
        main_box.pack_start(scrolled, True, True, 0)

        self.show_symlinks = False
        self.symlink_checkbox = Gtk.CheckButton(label="Show Symlinks")
        self.symlink_checkbox.set_active(False)  # Default off
        self.symlink_checkbox.connect("toggled", self.on_symlink_filter_toggled)
        sidebar_box.pack_start(self.symlink_checkbox, False, False, 0)

        self.show_symbolic = False
        self.toggle_checkbox = Gtk.CheckButton(label="Show Symbolic Icons")
        self.toggle_checkbox.set_active(False)  # Default off
        self.toggle_checkbox.connect("toggled", self.on_symbolic_filter_toggled)
        sidebar_box.pack_start(self.toggle_checkbox, False, False, 0)
                        
        self.create_symlink_btn = Gtk.Button(label="Create Symlink")
        self.create_symlink_btn.set_sensitive(False)  # disabled initially, until theme loaded
        self.create_symlink_btn.connect("clicked", self.on_create_symlink_clicked)
        sidebar_box.pack_start(self.create_symlink_btn, False, False, 0)


    def on_choose_theme(self, widget):
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
            self.create_symlink_btn.set_sensitive(True)  # Enable symlink button here
            threading.Thread(target=self.index_theme_icons, daemon=True).start()

        dialog.destroy()

    def index_theme_icons(self):
        if not self.theme_path:
            return

        # Temporary dict: icon_name -> dict of {ext: full_path}
        icons_found = {}

        for root, dirs, files in os.walk(self.theme_path):
            for f in files:
                if f.endswith((".svg", ".png")):
                    icon_name, ext = os.path.splitext(f)
                    if icon_name not in icons_found:
                        icons_found[icon_name] = {}
                    icons_found[icon_name][ext.lower()] = os.path.join(root, f)

        # Build final icon_index preferring svg over png
        idx = {}
        for icon_name, exts in icons_found.items():
            if ".svg" in exts:
                idx[icon_name] = exts[".svg"]
            elif ".png" in exts:
                idx[icon_name] = exts[".png"]

        GLib.idle_add(self.update_icon_index, idx)

    def update_icon_index(self, idx):
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

    def refresh_icon(self, changed_filename):
        base_name, _ = os.path.splitext(changed_filename)

        if self.current_category:
            self.load_icons(self.current_category)

        return False 

    def on_category_selected(self, listbox, row):
        if not row or not self.indexing_done:
            return

        category_name = row.get_child().get_text()
        self.current_category = category_name
        self.load_icons(category_name)

    def load_icons(self, category_name):
        #print(f"Loading icons for category: {category_name}") #debug
        self.flowbox.foreach(lambda child: self.flowbox.remove(child))
        self.icon_boxes = []
        icon_names = self.icon_categories.get(category_name, [])

        for icon_name in icon_names:
            icon_path = self.icon_index.get(icon_name, PLACEHOLDER_PATH)

            # Skip symlinks if filter is enabled
            if not self.show_symlinks and os.path.islink(icon_path):
                continue

            # Skip symbolic icons if filter is enabled
            if not self.show_symbolic and "symbolic" in icon_name:
                continue

            box = LazyIconBox(icon_name, icon_path, self.on_icon_clicked)
            self.flowbox.add(box)
            self.icon_boxes.append(box)

        self.show_all()

    def on_icon_clicked(self, icon_path, icon_name):
        if not self.theme_path or not self.current_category:
            print("Theme or category not selected")
            return

        # Determine base directory with current category + "96"
        base_dir = os.path.join(self.theme_path, self.current_category, "96")
        if not os.path.isdir(base_dir):
            # Fallback to fallback/96 if category folder doesn't exist
            base_dir = os.path.join(self.theme_path, "fallback", "96")

        os.makedirs(base_dir, exist_ok=True)

        new_icon_path = os.path.join(base_dir, icon_name + ".svg")

        # If icon doesn't exist, create from template
        if not os.path.exists(new_icon_path):
            try:
                shutil.copy2(TEMPLATE_SVG, new_icon_path)
                print(f"Created new icon from template: {new_icon_path}")
            except Exception as e:
                print(f"Failed to copy template: {e}")
                return

        # Open SVG in Inkscape
        subprocess.Popen(["inkscape", new_icon_path])

        def watch_and_generate():
            last_mtime = os.path.getmtime(new_icon_path)
            print(f"Watching {new_icon_path} for changes...")
            while True:
                time.sleep(2)
                try:
                    mtime = os.path.getmtime(new_icon_path)
                except FileNotFoundError:
                    print(f"Icon file deleted: {new_icon_path}")
                    break
                if mtime != last_mtime:
                    last_mtime = mtime
                    print(f"Detected change in {new_icon_path}, generating bitmaps...")
                    self.generate_bitmaps(new_icon_path)
                    # After generating bitmaps, update UI on main thread:
                    GLib.idle_add(self.refresh_icon, os.path.basename(new_icon_path))
                    break

        threading.Thread(target=watch_and_generate, daemon=True).start()

    def generate_bitmaps(self, svg_path):
        base_name = os.path.splitext(os.path.basename(svg_path))[0]

        # Find category for this icon
        category = None
        for cat, icons in self.icon_categories.items():
            if base_name in icons:
                category = cat
                break
        if not category:
            category = "fallback"  # fallback category if not found

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
                print(f"Exported {out_png}")
            except subprocess.CalledProcessError as e:
                print(f"Failed to export bitmap for size {size}: {e}")

        # --- Add this at the end ---
        # Update icon_index to include the new svg icon path (prefer SVG)
        self.icon_index[base_name] = svg_path

        # Refresh icons on main GTK thread for current category
        if self.current_category:
            GLib.idle_add(self.load_icons, self.current_category)


    def show_message(self, title, message):
        md = Gtk.MessageDialog(parent=self, flags=0, message_type=Gtk.MessageType.INFO,
                            buttons=Gtk.ButtonsType.OK, text=title)
        md.format_secondary_text(message)
        md.run()
        md.destroy()

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


    def on_create_symlink_clicked(self, button):
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
        else:
            dialog.destroy()

    def create_symlinks(self, category, src_name, tgt_name):
        if not self.theme_path:
            self.show_message("Error", "No icon theme loaded.")
            return

        category_path = os.path.join(self.theme_path, category)
        if not os.path.isdir(category_path):
            self.show_message("Error", f"Category folder '{category}' not found in theme.")
            return

        created_any = False
        errors = []

        # Detect available size directories inside category folder (e.g. 16, 22, 128, etc)
        available_sizes = [d for d in os.listdir(category_path) if d.isdigit() and os.path.isdir(os.path.join(category_path, d))]

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
                    print(f"Created SVG symlink: {tgt_svg} -> {rel_path}")
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
                    print(f"Created PNG symlink: {tgt_png} -> {rel_path}")
                    created_any = True
                except Exception as e:
                    errors.append(f"Error creating symlink {tgt_png}: {e}")

        if created_any:
            self.show_message("Success", f"Symlinks created for target '{tgt_name}' in category '{category}'.")
        else:
            self.show_message("Warning", "No source files found to create symlinks.")

        if errors:
            self.show_message("Errors", "\n".join(errors))

if __name__ == "__main__":
    win = IconThemeHelper()
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


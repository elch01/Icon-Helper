#!/usr/bin/env python3
"""
migrate_mintx_to_minty.py

Migrate Mint-X SVG icons into the Mint-Y master SVG template format,
replicating the imported icon onto every baseplate in the template and
preserving the template baseplates themselves (they are kept in the output
as visual guides) — but hidden by default.

This variant adds optional pruning of elements that are entirely outside the
source SVG's viewBox. The pruning uses Inkscape's CLI to query element bounding
boxes and removes elements whose bbox doesn't intersect the viewBox.

Usage additions:
  --prune-outside         Remove elements whose bbox is fully outside the source viewBox.
  --prune-margin FLOAT    Margin in user units to expand the viewBox before pruning (default 2.0).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import shutil
import subprocess
from typing import List, Tuple, Optional
from xml.etree import ElementTree as ET

# Prefer lxml for pretty output if available
try:
    from lxml import etree as LET  # type: ignore
    LXML_AVAILABLE = True
except Exception:
    LET = None
    LXML_AVAILABLE = False

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)


def parse_length(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    s = s.strip()
    if s == "":
        return None
    m = re.match(r"([0-9.+-eE]+)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def clone_element(elem: ET.Element) -> ET.Element:
    """Deep clone via serialization to avoid cross-tree references."""
    raw = ET.tostring(elem, encoding="utf-8")
    return ET.fromstring(raw)


def get_viewbox(root: ET.Element) -> Optional[Tuple[float, float, float, float]]:
    vb = root.get("viewBox")
    if vb:
        parts = re.split(r"[,\s]+", vb.strip())
        if len(parts) == 4:
            try:
                return tuple(float(p) for p in parts)
            except Exception:
                return None
    # fallback: try width/height as bbox origin (0,0)
    w = parse_length(root.get("width"))
    h = parse_length(root.get("height"))
    if w is not None and h is not None:
        return (0.0, 0.0, w, h)
    return None


def find_largest_rect(root: ET.Element) -> Tuple[Optional[ET.Element], Optional[float], Optional[float], Optional[float], Optional[float]]:
    best = None
    best_area = -1.0
    for rect in root.findall(".//{http://www.w3.org/2000/svg}rect"):
        w = parse_length(rect.get("width"))
        h = parse_length(rect.get("height"))
        if w is None or h is None:
            continue
        x = parse_length(rect.get("x")) or 0.0
        y = parse_length(rect.get("y")) or 0.0
        area = w * h
        if area > best_area:
            best_area = area
            best = (rect, x, y, w, h)
    if best is None:
        return (None, None, None, None, None)
    return best  # type: ignore


def find_baseplate_rects(template_root: ET.Element) -> List[Tuple[str, ET.Element, float, float, float, float]]:
    out = []
    for r in template_root.findall(".//{http://www.w3.org/2000/svg}rect"):
        rid = r.get("id") or ""
        m = re.match(r"rect(\d+)x(\d+)$", rid)
        if not m:
            continue
        w = parse_length(r.get("width"))
        h = parse_length(r.get("height"))
        if w is None or h is None:
            continue
        x = parse_length(r.get("x")) or 0.0
        y = parse_length(r.get("y")) or 0.0
        out.append((rid, r, x, y, w, h))
    out.sort(key=lambda it: it[4] * it[5])
    return out


def extract_source_graphics(src_root: ET.Element) -> List[ET.Element]:
    out = []
    for child in list(src_root):
        if child.tag in ("{%s}defs" % SVG_NS, "{%s}metadata" % SVG_NS):
            continue
        out.append(clone_element(child))
    return out


def extract_defs(root: ET.Element) -> Optional[ET.Element]:
    d = root.find("{%s}defs" % SVG_NS)
    if d is None:
        return None
    c = clone_element(d)
    c.tag = "{%s}defs" % SVG_NS
    return c


def find_text_by_id(root: ET.Element, idval: str) -> Optional[ET.Element]:
    for el in root.findall(".//{http://www.w3.org/2000/svg}text"):
        if el.get("id") == idval:
            return clone_element(el)
    for el in root.findall(".//*[@id]"):
        if el.get("id") == idval:
            return clone_element(el)
    return None


def set_text_content(text_elem: ET.Element, new_text: str) -> None:
    tspans = [c for c in list(text_elem) if c.tag.endswith("tspan") or c.tag.endswith("}tspan")]
    if tspans:
        tspans[0].text = new_text
        for t in tspans[1:]:
            t.text = ""
    else:
        text_elem.text = new_text


def create_output_root_from_template(template_root: ET.Element) -> ET.Element:
    attrib = {}
    for k, v in template_root.attrib.items():
        attrib[k] = v
    return ET.Element("{%s}svg" % SVG_NS, attrib)


def compute_transform_for_placement(src_root: ET.Element, place_x: float, place_y: float, place_w: float, place_h: float) -> Tuple[str, bool]:
    vb = get_viewbox(src_root)
    if vb:
        minx, miny, src_w, src_h = vb
    else:
        src_w = parse_length(src_root.get("width"))
        src_h = parse_length(src_root.get("height"))
        minx = 0.0
        miny = 0.0
        if src_w is None or src_h is None:
            return "", False

    if src_w and src_h:
        sx = place_w / src_w
        sy = place_h / src_h
        s = min(sx, sy)
        extra_x = (place_w - src_w * s) / 2.0
        extra_y = (place_h - src_h * s) / 2.0
        final_tx = place_x + extra_x - (minx * s)
        final_ty = place_y + extra_y - (miny * s)
        t = "translate({:.6f},{:.6f}) scale({:.6f})".format(final_tx, final_ty, s)
        return t, True
    return "", False


def write_svg(root: ET.Element, out_path: str):
    if LXML_AVAILABLE:
        txt = ET.tostring(root, encoding="utf-8")
        lroot = LET.fromstring(txt)
        content = LET.tostring(lroot, pretty_print=True, xml_declaration=True, encoding="utf-8")
        with open(out_path, "wb") as f:
            f.write(content)
    else:
        xml = ET.tostring(root, encoding="utf-8")
        with open(out_path, "wb") as f:
            f.write(b"<?xml version='1.0' encoding='UTF-8'?>\n")
            f.write(xml)


def _hide_element_display(elem: ET.Element) -> None:
    existing = elem.get("style") or ""
    if re.search(r"(?:^|;)\s*display\s*:", existing):
        new = re.sub(r"((?:^|;)\s*display\s*:\s*)[^;]+", r"\1none", existing)
    else:
        if existing and not existing.endswith(";"):
            existing = existing + ";"
        new = existing + "display:none;"
    elem.set("style", new)
    elem.set("display", "none")


def inkscape_query_all(svg_path: str) -> Optional[List[Tuple[str, float, float, float, float]]]:
    """
    Query Inkscape for bounding boxes of elements in svg_path.

    Returns list of tuples: (id, x, y, w, h)
    or None if inkscape not available or query fails.

    This function tolerantly parses a few common output formats that Inkscape
    historically uses for --query-all.
    """
    cmd = shutil.which("inkscape")
    if not cmd:
        return None
    # Call Inkscape --query-all
    try:
        # use --query-all (works with Inkscape 1.x). If user has an older or newer
        # version, output parsing below tries to be tolerant.
        proc = subprocess.run([cmd, "--query-all", svg_path], capture_output=True, text=True, check=False)
        out = proc.stdout.strip()
        if not out:
            # no output -> nothing to parse
            return []
    except Exception:
        return None

    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    results = []
    for ln in lines:
        # tolerate several separators: comma, colon, whitespace
        # formats seen: "id,x,y,w,h"  OR "id: x : y : w : h" OR "id x y w h"
        # Try CSV first
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) == 5:
            ident = parts[0]
            try:
                x = float(parts[1])
                y = float(parts[2])
                w = float(parts[3])
                h = float(parts[4])
                results.append((ident, x, y, w, h))
                continue
            except Exception:
                pass
        # Try colon separated
        if ":" in ln:
            p2 = [p.strip() for p in ln.split(":")]
            if len(p2) >= 5:
                ident = p2[0]
                try:
                    x = float(p2[1])
                    y = float(p2[2])
                    w = float(p2[3])
                    h = float(p2[4])
                    results.append((ident, x, y, w, h))
                    continue
                except Exception:
                    pass
        # fallback: whitespace separated
        sp = re.split(r"\s+", ln)
        if len(sp) >= 5:
            ident = sp[0]
            try:
                x = float(sp[1])
                y = float(sp[2])
                w = float(sp[3])
                h = float(sp[4])
                results.append((ident, x, y, w, h))
                continue
            except Exception:
                pass
        # if nothing matched, skip line
    return results


def prune_elements_outside_viewbox(svg_tree: ET.ElementTree, svg_path: str, margin: float = 2.0) -> None:
    """
    Remove elements from svg_tree whose Inkscape-reported bbox is fully outside the viewBox (expanded by margin).
    Operates in-place on svg_tree.

    Requirements: inkscape on PATH. Elements must have ids to be removable.
    """
    boxes = inkscape_query_all(svg_path)
    if boxes is None:
        print("  prune: Inkscape not found or query failed; skipping pruning.", file=sys.stderr)
        return
    if not boxes:
        print("  prune: inkscape returned no element bbox data; skipping pruning.", file=sys.stderr)
        return

    root = svg_tree.getroot()
    vb = get_viewbox(root)
    if not vb:
        print("  prune: source SVG has no viewBox or usable width/height; skipping pruning.", file=sys.stderr)
        return
    vminx, vminy, vw, vh = vb
    # expand viewBox by margin
    vminx -= margin
    vminy -= margin
    vw += 2 * margin
    vh += 2 * margin
    vmaxx = vminx + vw
    vmaxy = vminy + vh

    # For each reported element bbox, remove the element if box is fully outside viewBox
    removed_any = False
    for ident, x, y, w, h in boxes:
        if w <= 0 or h <= 0:
            continue
        ex1 = x
        ey1 = y
        ex2 = x + w
        ey2 = y + h
        # If bbox intersects expanded viewBox, keep element
        intersects = not (ex2 < vminx or ex1 > vmaxx or ey2 < vminy or ey1 > vmaxy)
        if not intersects:
            # remove element with this id
            # find any element with that id
            el = root.find(".//*[@id='%s']" % ident)
            if el is not None:
                parent = svg_tree.getroot()
                # find parent properly by traversing (ElementTree lacks parent pointers)
                parent_found = None
                for p in root.iter():
                    for child in list(p):
                        if child is el:
                            parent_found = p
                            break
                    if parent_found is not None:
                        break
                if parent_found is not None:
                    parent_found.remove(el)
                    removed_any = True
                    print(f"  prune: removed element id='{ident}' bbox=({x},{y},{w},{h}) fully outside viewBox", file=sys.stderr)
                else:
                    # fallback: try remove by searching top-level children
                    try:
                        root.remove(el)
                        removed_any = True
                        print(f"  prune: removed element id='{ident}' (top-level) fully outside viewBox", file=sys.stderr)
                    except Exception:
                        pass
            else:
                # can't find element by id in XML: skip
                pass
    if not removed_any:
        print("  prune: no removable elements found outside viewBox.", file=sys.stderr)


def migrate_one(source_svg_path: str, template_root: ET.Element, out_path: str,
                no_replicate: bool = False, target_rect_id: Optional[str] = None,
                preserve_template_defs: bool = False, dry_run: bool = False,
                category_override: Optional[str] = None,
                src_root_for_category: Optional[str] = None,
                prune_outside: bool = False, prune_margin: float = 2.0) -> None:
    print(f"Migrating: {source_svg_path} -> {out_path}")
    try:
        src_tree = ET.parse(source_svg_path)
        src_root = src_tree.getroot()
    except Exception as e:
        print(f"  ERROR: failed to parse source SVG: {e}", file=sys.stderr)
        return

    if prune_outside:
        prune_elements_outside_viewbox(src_tree, source_svg_path, margin=prune_margin)
        # re-get root after potential mutation
        src_root = src_tree.getroot()

    icon_name = os.path.splitext(os.path.basename(source_svg_path))[0]

    category = "apps"
    if category_override:
        category = category_override
    elif src_root_for_category:
        try:
            rel = os.path.relpath(source_svg_path, start=src_root_for_category)
            parts = rel.split(os.sep)
            if len(parts) >= 2:
                category = parts[0]
            else:
                category = "apps"
        except Exception:
            category = "apps"

    baseplate_rects = []
    if target_rect_id:
        r = template_root.find(".//*[@id='%s']" % target_rect_id)
        if r is not None and r.tag == "{%s}rect" % SVG_NS:
            w = parse_length(r.get("width"))
            h = parse_length(r.get("height"))
            if w is not None and h is not None:
                x = parse_length(r.get("x")) or 0.0
                y = parse_length(r.get("y")) or 0.0
                baseplate_rects.append((r.get("id"), r, x, y, w, h))
    if not baseplate_rects:
        baseplate_rects = find_baseplate_rects(template_root)

    if not baseplate_rects:
        r, rx, ry, rw, rh = find_largest_rect(template_root)
        if r is not None:
            baseplate_rects = [(r.get("id") or "largest_rect", r, rx, ry, rw, rh)]

    if no_replicate and baseplate_rects:
        baseplate_rects.sort(key=lambda it: it[4] * it[5], reverse=True)
        baseplate_rects = [baseplate_rects[0]]

    out_root = create_output_root_from_template(template_root)

    tmeta = template_root.find("{%s}metadata" % SVG_NS)
    if tmeta is not None:
        out_root.append(clone_element(tmeta))

    if preserve_template_defs:
        tpl_defs = template_root.find("{%s}defs" % SVG_NS)
        if tpl_defs is not None:
            out_root.append(clone_element(tpl_defs))
        else:
            out_root.append(ET.Element("{%s}defs" % SVG_NS))
    else:
        src_defs = extract_defs(src_root)
        if src_defs is not None:
            out_root.append(src_defs)
        else:
            out_root.append(ET.Element("{%s}defs" % SVG_NS))

    kept_rect_ids = []
    for rid, rect_elem, rx, ry, rw, rh in baseplate_rects:
        crect = clone_element(rect_elem)
        _hide_element_display(crect)
        out_root.append(crect)
        kept_rect_ids.append((rid, rx, ry, rw, rh))

    for tid, value in (("context", category), ("icon-name", icon_name)):
        txt = find_text_by_id(template_root, tid)
        if txt is not None:
            set_text_content(txt, value)
            _hide_element_display(txt)
            out_root.append(txt)
        else:
            t = ET.Element("{%s}text" % SVG_NS, {"id": tid})
            t.text = value
            _hide_element_display(t)
            out_root.append(t)

    source_items = extract_source_graphics(src_root)
    if not source_items:
        print("  WARNING: no drawable elements found in source SVG.", file=sys.stderr)

    placements = []
    for rid, rx, ry, rw, rh in kept_rect_ids:
        gid = "ImportedIcon-%s" % (rid or "unknown")
        g = ET.Element("{%s}g" % SVG_NS, {"id": gid})
        for it in source_items:
            g.append(clone_element(it))
        transform, ok = compute_transform_for_placement(src_root, rx, ry, rw, rh)
        if transform:
            g.set("transform", transform)
        else:
            g.set("transform", "translate({:.6f},{:.6f})".format(rx, ry))
        out_root.append(g)
        placements.append((rid, rx, ry, rw, rh, transform))

    if not placements and source_items:
        g = ET.Element("{%s}g" % SVG_NS, {"id": "ImportedIcon"})
        for it in source_items:
            g.append(clone_element(it))
        out_root.append(g)
        print("  NOTE: no baseplates kept; appended imported graphics without placement.", file=sys.stderr)

    if dry_run:
        print("  DRY RUN: would write output to:", out_path)
    else:
        out_dir = os.path.dirname(out_path)
        if out_dir and not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        try:
            write_svg(out_root, out_path)
            print("  -> wrote:", out_path)
            if placements:
                print("  Placements:")
                for (rid, rx, ry, rw, rh, transform) in placements:
                    print("   - id=%s x=%s y=%s w=%s h=%s transform=%s" % (rid, rx, ry, rw, rh, transform))
            print("  icon-name set to:", icon_name, "  context set to:", category)
        except Exception as e:
            print(f"  ERROR writing output SVG: {e}", file=sys.stderr)


def collect_source_files(src_path: str, exts: List[str]) -> List[str]:
    out: List[str] = []
    if os.path.isfile(src_path):
        if os.path.islink(src_path):
            print(f"Skipping source file because it is a symlink: {src_path}", file=sys.stderr)
            return []
        out.append(src_path)
        return out
    for root, dirs, files in os.walk(src_path):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for fn in files:
            full = os.path.join(root, fn)
            if os.path.islink(full):
                print(f"Skipping symlink file: {full}", file=sys.stderr)
                continue
            if any(fn.lower().endswith(ext.lower()) for ext in exts):
                out.append(full)
    out.sort()
    return out


def main():
    p = argparse.ArgumentParser(description="Migrate Mint-X SVG(s) to Mint-Y master SVG format with optional pruning of outside elements.")
    p.add_argument("source", help="Source SVG file or directory containing SVGs")
    p.add_argument("template", help="Mint-Y master template SVG file")
    p.add_argument("output", help="Output file path (for single source file) or output directory (for source directory)")
    p.add_argument("--no-replicate", action="store_true", help="Place icon only into the largest baseplate")
    p.add_argument("--target-rect-id", help="Only place into the rect with this id (still respects --no-replicate)")
    p.add_argument("--preserve-tpl-defs", action="store_true", help="Preserve template <defs> instead of copying source <defs>")
    p.add_argument("--extensions", default="svg", help="Comma-separated extensions to include when source is a directory (default: svg)")
    p.add_argument("--dry-run", action="store_true", help="Don't write files; only print what would be done")
    p.add_argument("--category", help="Force context/category value (overrides inferred category when migrating a directory)")
    p.add_argument("--prune-outside", action="store_true", help="Remove elements whose bbox is fully outside the source viewBox (requires inkscape)")
    p.add_argument("--prune-margin", type=float, default=2.0, help="Margin (user units) to expand viewBox before pruning (default 2.0)")
    args = p.parse_args()

    if not os.path.isfile(args.template):
        print("ERROR: template file does not exist:", args.template, file=sys.stderr)
        sys.exit(2)

    try:
        tpl_tree = ET.parse(args.template)
        tpl_root = tpl_tree.getroot()
    except Exception as e:
        print("ERROR: failed to parse template SVG:", e, file=sys.stderr)
        sys.exit(2)

    exts = [("." + e.strip().lstrip(".")).lower() for e in args.extensions.split(",") if e.strip()]

    sources = collect_source_files(args.source, exts)
    if not sources:
        print("ERROR: no source SVG files found at (or all were symlinks):", args.source, file=sys.stderr)
        sys.exit(2)

    if os.path.isfile(args.source):
        if os.path.islink(args.source):
            print("ERROR: source is a symlink; skipping.", file=sys.stderr)
            sys.exit(2)
        if os.path.isdir(args.output):
            out_file = os.path.join(args.output, os.path.basename(args.source))
        else:
            out_file = args.output
        migrate_one(args.source, tpl_root, out_file, no_replicate=args.no_replicate,
                    target_rect_id=args.target_rect_id, preserve_template_defs=args.preserve_tpl_defs,
                    dry_run=args.dry_run, category_override=args.category,
                    src_root_for_category=None, prune_outside=args.prune_outside, prune_margin=args.prune_margin)
    else:
        if os.path.isfile(args.output):
            print("ERROR: when source is a directory, output must be a directory (not a file).", file=sys.stderr)
            sys.exit(2)
        if not os.path.exists(args.output) and not args.dry_run:
            os.makedirs(args.output, exist_ok=True)

        src_root_abspath = os.path.abspath(args.source)
        out_root_abspath = os.path.abspath(args.output)
        for src in sources:
            rel = os.path.relpath(src, src_root_abspath)
            out_path = os.path.join(out_root_abspath, rel)
            out_dir = os.path.dirname(out_path)
            if not args.dry_run:
                os.makedirs(out_dir, exist_ok=True)
            migrate_one(src, tpl_root, out_path, no_replicate=args.no_replicate,
                        target_rect_id=args.target_rect_id, preserve_template_defs=args.preserve_tpl_defs,
                        dry_run=args.dry_run, category_override=args.category,
                        src_root_for_category=src_root_abspath, prune_outside=args.prune_outside, prune_margin=args.prune_margin)


if __name__ == "__main__":
    main()

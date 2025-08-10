#!/usr/bin/env python3
import os
import json
import argparse

# Known freedesktop categories (lowercase)
KNOWN_CATEGORIES = {
    "actions", "animations", "apps", "applications", "categories",
    "devices", "emblems", "emotes", "intl", "mimetypes",
    "places", "status"
}
SPECIAL_DIRS = {"scalable"}

def is_size_dir(name):
    return "x" in name and name.replace("x", "").isdigit()

def detect_category(path_parts):
    for part in path_parts:
        if part.lower() in KNOWN_CATEGORIES:
            if part.lower() == "applications":
                return "apps"
            return part.lower()
    return None

def generate_icon_index(theme_path):
    categories = {}
    for root, dirs, files in os.walk(theme_path):
        rel_path = os.path.relpath(root, theme_path)
        parts = rel_path.split(os.sep)
        if rel_path in (".", ""):
            continue
        category = detect_category(parts)
        if not category:
            continue
        if category not in categories:
            categories[category] = set()
        for f in files:
            if f.endswith((".svg", ".png")):
                icon_name = os.path.splitext(f)[0]
                categories[category].add(icon_name)
    return {cat: sorted(list(names)) for cat, names in categories.items()}

def merge_indexes(base, merging):
    merged = {}
    all_keys = set(base.keys()) | set(merging.keys())
    for key in all_keys:
        base_list = base.get(key, [])
        merging_list = merging.get(key, [])
        if base_list and merging_list:
            # merge without duplicates
            merged[key] = sorted(set(base_list) | set(merging_list))
        elif base_list:
            merged[key] = base_list
        else:
            merged[key] = merging_list
    return merged


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate and optionally merge icon indexes.")
    parser.add_argument("-t", "--theme", required=True, help="Path to the main icon theme folder")
    parser.add_argument("-m", "--merge", help="Optional second theme folder to merge")
    parser.add_argument("-o", "--output", default="icon_categories.json", help="Output JSON file")
    args = parser.parse_args()

    base_index = generate_icon_index(args.theme)

    if args.merge:
        merge_index_data = generate_icon_index(args.merge)
        final_index = merge_indexes(base_index, merge_index_data)
    else:
        final_index = base_index

    with open(args.output, 'w') as out:
        json.dump(final_index, out, indent=4)

    print(f"✅ Icon index written to {args.output}")


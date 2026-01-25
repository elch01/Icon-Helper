# migrate_mintx_to_minty

Migrate Mint‑X SVG icons into a Mint‑Y master SVG template.

This tool converts single Mint‑X SVG files or entire directories of icons into Mint‑Y master SVGs ready for generating bitmaps. It:

- Imports drawing elements from Mint‑X icons into the Mint‑Y template.
- Places the imported artwork on every baseplate size found in the template (16×16, 24×24, 32×32, 48×48, 64×64, 96×96, 256×256, etc.).
- Populates the template `icon-name` and `context` text objects (hidden in the output).
- Keeps template baseplates in the output as hidden guides.
- Optionally prunes exporter/dev placeholder elements that sit completely outside the source viewBox (uses Inkscape for accurate per-element bounding boxes).
- Recursively migrates a directory and replicates the source directory structure in the output.
- Skips filesystem symlinks when crawling source directories.

This README explains requirements, installation, usage, options, recommended workflow and troubleshooting.

---

## Requirements

- Python 3.7+ (3.8+ recommended)
- Optional (for prettier output): `lxml`
  - Install: `pip install lxml`
- Optional (for pruning outside elements): Inkscape 1.x on your PATH
  - The pruning feature uses `inkscape --query-all` to obtain per-element bounding boxes.

The script is a single-file CLI: `migrate_mintx_to_minty.py`.

---

## Installation

1. Copy `migrate_mintx_to_minty.py` into a directory in your PATH or into your project.
2. Make it executable (optional):
   ```
   chmod +x migrate_mintx_to_minty.py
   ```
3. (Optional) Install lxml for prettier generated SVGs:
   ```
   pip install lxml
   ```

---

## Quick examples

Single file:
```
./migrate_mintx_to_minty.py mint-x-input.svg mint-y-template.svg mint-x-input-migrated.svg
```

Directory (recursive):
```
./migrate_mintx_to_minty.py icons-src/ mint-y-template.svg out-icons/
```
This will walk `icons-src/` recursively, skip symlinks, and write matched migrated SVGs under `out-icons/` preserving the directory tree.

Dry run (no files written):
```
./migrate_mintx_to_minty.py icons-src/ mint-y-template.svg out-icons/ --dry-run
```

Prune elements outside viewBox (requires Inkscape on PATH):
```
./migrate_mintx_to_minty.py icons-src/ mint-y-template.svg out-icons/ --prune-outside --prune-margin 2.0
```

Only place icon in the largest baseplate (do not replicate across all baseplates):
```
./migrate_mintx_to_minty.py icon.svg template.svg out.svg --no-replicate
```

Only target a specific baseplate rect id in the template:
```
./migrate_mintx_to_minty.py icon.svg template.svg out.svg --target-rect-id rect48x48
```

Force category/context value instead of inferring from directory:
```
./migrate_mintx_to_minty.py icon.svg template.svg out.svg --category actions
```

Limit file extensions when scanning directories (comma separated):
```
./migrate_mintx_to_minty.py icons-src/ template.svg out/ --extensions svg,svgz
```

---

## Behavior & implementation details

- Template preservation
  - The script copies only the template metadata and the `icon-name` and `context` text elements (if present). Everything else in the template is not copied over except the baseplate `rect` elements which are cloned and kept as hidden guides.
  - Cloned baseplate rectangles are hidden by adding `display:none;` (and `display="none"` attribute) so they remain in the SVG but won't be visible in renderings.

- icon-name & context
  - `icon-name` is set to the basename of the source file (filename without extension).
  - `context` is set to the category (default `apps`), derived in this order:
    - `--category` override (highest priority)
    - If migrating a directory: first path component of the file's relative path under the source root (e.g. `apps/gtk/my-icon.svg` → `apps`)
    - Fallback: `apps`
  - Both `icon-name` and `context` are hidden in the output (present but not visible).

- Source defs and drawing elements
  - The source SVG `<defs>` are copied into the output (unless `--preserve-tpl-defs` is used to keep the template's defs instead).
  - All top-level drawing elements (everything except `<defs>` and `<metadata>`) are imported and placed into groups named `ImportedIcon-<rectId>` for each baseplate.

- Transform and placement
  - If the source SVG has a `viewBox` that is used to compute scaling, the imported graphics are scaled and translated to fit and be centered in each target baseplate rectangle.
  - If the source lacks a `viewBox` or width/height information, the script will fall back to translating the icon to the baseplate origin without scaling.

- Pruning outside elements (optional)
  - When `--prune-outside` is used, the script calls Inkscape to get per-element bounding boxes via `inkscape --query-all`.
  - Any element whose bounding box is fully outside the source viewBox (optionally expanded by `--prune-margin`) is removed — but only if the element has an ID (Inkscape returns element ids).
  - Elements partially intersecting the viewBox are kept.
  - If Inkscape is not available the script warns and skips pruning.

- Symlink handling
  - When scanning directories, the script does not follow symlinked directories and does not include symlinked files in the migration set. This avoids duplicate/mirrored copies and accidental traversal.

---

## Recommended workflow

1. Run a dry run first to see what will change:
   ```
   ./migrate_mintx_to_minty.py icons-src/ template.svg out/ --dry-run --prune-outside
   ```

2. Inspect the dry-run output (the script prints what it would do) and tweak `--prune-margin` if elements near the edges are being removed unexpectedly.

3. Run actual migration:
   ```
   ./migrate_mintx_to_minty.py icons-src/ template.svg out/ --prune-outside --prune-margin 2.0
   ```

4. Spot-check a few generated SVGs in Inkscape or your preferred viewer.

5. If you want a conservative approach, omit `--prune-outside` and manually clean only a subset of icons.

---

## Troubleshooting

- Inkscape not found or pruning not effective
  - Ensure `inkscape` is on your PATH. Calling `inkscape --version` should print the version.
  - Some platforms or distributions may install the binary with a different name; if so, either add a compatible wrapper or let me know and I can add a CLI flag to point to a custom inkscape path.

- Elements without ids not pruned
  - Inkscape reports per-element bounding boxes keyed by element IDs. If elements lack an `id`, they cannot be removed by the pruning step. If you want broader pruning, the script can optionally assign temporary IDs to elements before querying — but that can be risky (it mutates the source) so request it explicitly.

- Wrong scaling or unexpected artwork placement
  - Confirm the source SVG has a correct `viewBox` (recommended). If not, the script falls back to using `width`/`height` — but complex or non‑standard source SVGs may require manual inspection.

- Files not processed / skipped
  - Symlinks are skipped intentionally. Ensure you point the script to actual files.
  - Use `--extensions` to include non-standard SVG file extensions.

---

## Output file notes

- Output SVGs will:
  - Contain the template's baseplate rects (hidden).
  - Contain the template `icon-name` and `context` elements set to the migrated icon's name and category (hidden).
  - Contain a cloned `<defs>` (from the source by default).
  - Contain imported artwork groups named `ImportedIcon-<rectId>` for each baseplate.

- Naming & structure
  - When processing a directory, the script preserves relative paths and writes the migrated SVG at the same relative path under the output directory.

---

## Advanced ideas (can be added)

- Save pruned elements to a per-icon "archive" SVG instead of deleting them.
- Produce a CSV/JSON report listing pruned element ids and bboxes.
- Auto-assign temporary ids to elements without ids before pruning (risky).
- Parallelize directory processing for speed.
- Add a `--verbose` flag to dump more internal diagnostics.

If you'd like any of the above added, tell me which and I’ll extend the script.

---

## License

GPL v3.0

---

If you want, I can also generate a short manpage, quick reference card, or example CI job (GitHub Actions) that runs the migration and validates outputs.

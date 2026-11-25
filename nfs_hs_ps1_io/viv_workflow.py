"""BIGF VIV vehicle import/export helpers for the NFS HS PS1 add-on."""

import json
import os
import struct
from collections import Counter
from array import array
import tempfile

import bpy
from bpy.types import Operator
from bpy.props import StringProperty, EnumProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper, axis_conversion

from . import import_nfshs_ps1_models as geo_import
from . import export_nfshs_ps1_models as geo_export


def default_geo_matrix():
    return axis_conversion(from_forward='Z', from_up='Y', to_forward='-Y', to_up='Z').to_4x4()


def read_bigf(path):
    data = open(path, "rb").read()
    if len(data) < 16 or data[:4] != b"BIGF":
        raise ValueError("Unsupported VIV archive (expected BIGF)")
    count = struct.unpack_from(">I", data, 8)[0]
    table_end = struct.unpack_from(">I", data, 12)[0]
    if count > 10000 or table_end < 16 or table_end > len(data):
        raise ValueError("Invalid BIGF directory")
    entries, pos = [], 16
    for _ in range(count):
        if pos + 8 > table_end:
            raise ValueError("Truncated BIGF directory")
        offset, size = struct.unpack_from(">II", data, pos)
        end = data.find(b"\0", pos + 8, table_end)
        if end < 0:
            raise ValueError("Invalid BIGF filename")
        name = data[pos + 8:end].decode("ascii", "replace")
        if offset + size > len(data):
            raise ValueError("BIGF member extends past archive")
        entries.append((name, data[offset:offset + size]))
        pos = end + 1
    return data, entries


def write_bigf(entries):
    directory_size = sum(9 + len(name.encode("ascii", "replace")) for name, _ in entries)
    data_offset = 16 + directory_size
    table, payloads, offset = bytearray(), [], data_offset
    for name, payload in entries:
        encoded = name.encode("ascii", "replace")[:255]
        table += struct.pack(">II", offset, len(payload)) + encoded + b"\0"
        payloads.append(payload)
        offset += len(payload)
    return b"BIGF" + struct.pack(">III", offset, len(entries), data_offset) + table + b"".join(payloads)


def _rgb555(r, g, b):
    # A zero PS1 CLUT word is transparent, so keep opaque black nonzero.
    value = (int(round(r * 31)) & 31) | ((int(round(g * 31)) & 31) << 5) | ((int(round(b * 31)) & 31) << 10)
    return value or 1


def _psh_edited(data, image):
    """Patch PSH pixels/palettes from its Blender 256x256 atlas, preserving layout."""
    if data[:4] != b"SHPP" or tuple(image.size) != (256, 256):
        return data
    rgba = array('f', [0.0]) * (256 * 256 * 4)
    image.pixels.foreach_get(rgba)
    out = bytearray(data)
    count = struct.unpack_from('<I', data, 8)[0]
    if count > 4096 or 16 + count * 8 > len(data):
        return data
    for i in range(count):
        ent = 16 + i * 8
        off = struct.unpack_from('<I', data, ent + 4)[0]
        if off + 16 > len(data):
            continue
        typ = data[off]
        depth_code = typ & 3
        if depth_code not in (0, 1, 2):
            continue
        depth = (4, 8, 16)[depth_code]
        w, h = struct.unpack_from('<hh', data, off + 4)
        pos = struct.unpack_from('<I', data, off + 12)[0]
        x0 = (pos & 0xFFF) * (4 if depth == 4 else 2 if depth == 8 else 1)
        y0 = (pos >> 16) & 0xFFF
        if w <= 0 or h <= 0 or x0 + w > 256 or y0 + h > 256:
            continue
        row_bytes = ((w * depth + 15) & ~15) // 8
        pix_off = off + 16
        if pix_off + row_bytes * h > len(data):
            continue
        colors, alpha_modes = [], []
        for y in range(h):
            row, mode_row = [], []
            for x in range(w):
                src = ((255 - (y0 + y)) * 256 + x0 + x) * 4
                c = tuple(max(0, min(31, int(round(rgba[src + k] * 31)))) for k in range(3))
                a = rgba[src + 3]
                row.append(c)
                mode_row.append(0 if a < 0.25 else (1 if a < 0.75 else 2))
            colors.append(row)
            alpha_modes.append(mode_row)
        if depth == 16:
            for y, row in enumerate(colors):
                for x, c in enumerate(row):
                    mode = alpha_modes[y][x]
                    rgb = c[0] | c[1] << 5 | c[2] << 10
                    word = 0 if mode == 0 else (rgb | (0x8000 if mode == 1 else 0))
                    if mode == 2 and rgb == 0:
                        word = 1
                    struct.pack_into('<H', out, pix_off + y * row_bytes + x * 2, word)
            continue
        next_off = data[off + 1] | (data[off + 2] << 8) | (data[off + 3] << 16)
        pal_off = off + next_off + 16
        pal_count = 16 if depth == 4 else 256
        if next_off < 16 or pal_off + pal_count * 2 > len(data):
            continue
        transparent = any(mode == 0 for row in alpha_modes for mode in row)
        freq = Counter((c, mode) for color_row, mode_row in zip(colors, alpha_modes)
                       for c, mode in zip(color_row, mode_row) if mode != 0)
        capacity = pal_count - (1 if transparent else 0)
        selected = []
        # Keep both opaque and STP states when the source atlas contains them.
        for mode in (1, 2):
            candidates = [(sample, amount) for sample, amount in freq.most_common() if sample[1] == mode]
            if candidates and len(selected) < capacity:
                selected.append(candidates[0][0])
        for sample, _ in freq.most_common():
            if sample not in selected and len(selected) < capacity:
                selected.append(sample)
        if not selected and not transparent:
            selected = [((0, 0, 0), 2)]
        palette = ([((0, 0, 0), 0)] if transparent else []) + selected
        palette += [((0, 0, 0), 2)] * (pal_count - len(palette))
        for pi, (c, mode) in enumerate(palette[:pal_count]):
            rgb = c[0] | c[1] << 5 | c[2] << 10
            word = 0 if mode == 0 else (rgb | (0x8000 if mode == 1 else 0))
            if mode == 2 and rgb == 0:
                word = 1
            struct.pack_into('<H', out, pal_off + pi * 2, word)
        mode_indices = {mode: [i for i, (_, item_mode) in enumerate(palette[:pal_count]) if item_mode == mode]
                        for mode in (1, 2)}
        for y, row in enumerate(colors):
            row_start = pix_off + y * row_bytes
            out[row_start:row_start + row_bytes] = b'\0' * row_bytes
            for x, c in enumerate(row):
                mode = alpha_modes[y][x]
                if mode == 0 and transparent:
                    idx = 0
                else:
                    candidates = mode_indices.get(mode) or mode_indices[2] or mode_indices[1]
                    idx = min(candidates, key=lambda n: sum((c[k] - palette[n][0][k]) ** 2 for k in range(3)))
                if depth == 4:
                    byte_at = row_start + x // 2
                    if x & 1:
                        out[byte_at] |= idx << 4
                    else:
                        out[byte_at] |= idx & 15
                else:
                    out[row_start + x] = idx & 255
    return bytes(out)


def vehicle_geos(entries):
    return [(name, payload) for name, payload in entries if name.lower().endswith(".geo")]


VEHICLE_NAMES = {
    "MSLK": "Mercedes-Benz SLK", "BMWZ": "BMW Z3", "HSVT": "Holden HSVT",
    "FORD": "Ford Falcon", "CMRO": "Chevrolet Camaro", "TRAM": "Pontiac Trans Am",
    "ADB7": "Aston Martin DB7", "JXKR": "Jaguar XKR", "BMWM": "BMW M5",
    "CORV": "Chevrolet Corvette", "F550": "Ferrari 550", "P993": "Porsche 911",
    "F375": "Ferrari F50", "DIAB": "Lamborghini Diablo SV", "MCLK": "Mercedes-Benz CLK",
    "MCF1": "McLaren F1", "X993": "Experimental Porsche 911", "X375": "Experimental Ferrari F50",
    "XCF1": "Experimental McLaren F1", "BNS1": "Bonus car 1", "BNS2": "Bonus car 2",
    "CCPR": "Chevrolet Caprice police car", "CHSV": "Holden HSVT police car",
    "CBMW": "BMW M5 police car", "CCOV": "Corvette police car", "CCMR": "Porsche police car",
    "CDBL": "Diablo police car", "CHOP": "Police helicopter", "GUAT": "Audi A4 traffic car",
    "BMW5": "BMW 530i traffic car", "MERC": "Mercedes-Benz 500SL traffic car",
    "WOLW": "Volvo wagon traffic car", "SBUS": "School bus", "SAND": "Toyota Land Cruiser",
    "RIAT": "Mazda Miata", "ARMY": "Army truck", "COMC": "Jeep Comanche",
    "TON1": "One-ton truck", "TON2": "One-ton truck (alternate)", "VAN": "Mercedes-Benz van",
    "BEEP": "Jeep Cherokee", "PKUP": "Pickup truck", "F500": "Ferrari 500",
    "CUCA": "Barracuda", "TAXI": "Taxi", "TBUS": "Tour bus", "LOGT": "Logging truck",
    "CBUS": "Transit bus",
}
_ENUM_ITEMS_CACHE = {}


def display_vehicle_name(geo_name):
    """Turn internal GEO stems such as zzbmwzh.geo into a readable model label."""
    code = os.path.splitext(os.path.basename(geo_name))[0].upper()
    if code.startswith("ZZ"):
        code = code[2:]
    variant = ""
    if code.endswith("H"):
        code, variant = code[:-1], " (H variant)"
    elif code.endswith("S"):
        code, variant = code[:-1], " (S variant)"
    return VEHICLE_NAMES.get(code, "Vehicle %s" % code) + variant


def read_bigf_names(path):
    """Read just the directory from a BIGF archive for the all-cars menu."""
    with open(path, "rb") as f:
        header = f.read(16)
        if len(header) < 16 or header[:4] != b"BIGF":
            return []
        count = struct.unpack_from(">I", header, 8)[0]
        directory_end = struct.unpack_from(">I", header, 12)[0]
        if count > 10000 or directory_end < 16 or directory_end > os.path.getsize(path):
            return []
        directory = f.read(directory_end - 16)
    names, pos = [], 0
    for _ in range(count):
        if pos + 8 > len(directory):
            return []
        end = directory.find(b"\0", pos + 8)
        if end < 0:
            return []
        names.append(directory[pos + 8:end].decode("ascii", "replace"))
        pos = end + 1
    return names


def enum_geos(self, context):
    try:
        directory = os.path.dirname(os.path.abspath(self.filepath or self.directory))
        records = []
        for entry in os.scandir(directory):
            if not entry.is_file() or not entry.name.lower().endswith(".viv"):
                continue
            try:
                for name in read_bigf_names(entry.path):
                    if name.lower().endswith(".geo"):
                        records.append((entry.name, name, display_vehicle_name(name)))
            except OSError:
                continue
        counts = Counter(label for _, _, label in records)
        records.sort(key=lambda row: (row[2].casefold(), row[0].casefold(), row[1].casefold()))
        items = []
        for i, (archive, geo, label) in enumerate(records):
            if counts[label] > 1:
                label = "%s — %s" % (label, archive)
            identifier = "%s::%s" % (archive, geo)
            description = "Archive: %s | GEO: %s" % (archive, geo)
            items.append((identifier, label, description, i))
        items = items or [("", "No vehicle GEO files found in this folder", "", 0)]
        _ENUM_ITEMS_CACHE[self.as_pointer()] = items
        return items
    except Exception:
        items = [("", "Select a valid BIGF VIV archive", "", 0)]
        _ENUM_ITEMS_CACHE[self.as_pointer()] = items
        return items


class ImportNFSVIV(Operator, ImportHelper):
    bl_idname = "import_nfshsps1.viv"
    bl_label = "Import vehicle from VIV"
    filename_ext = ".viv"
    filter_glob: StringProperty(default="*.viv", options={"HIDDEN"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    vehicle: EnumProperty(name="Vehicle", items=enum_geos)
    clear_scene: BoolProperty(name="Clear scene", default=True)
    mirror_left_right: BoolProperty(name="Mirror left/right", default=True)
    choose_vehicle: BoolProperty(default=False, options={"HIDDEN"})

    def invoke(self, context, event):
        return ImportHelper.invoke(self, context, event)

    def execute(self, context):
        if not self.choose_vehicle:
            self.choose_vehicle = True
            return context.window_manager.invoke_props_dialog(self, width=480)
        if not self.vehicle:
            self.report({"ERROR"}, "VIV contains no GEO vehicles")
            return {"CANCELLED"}
        try:
            archive_name, selected = self.vehicle.split("::", 1)
            archive_path = os.path.join(self.directory or os.path.dirname(self.filepath), archive_name)
            _, entries = read_bigf(archive_path)
            if selected is None:
                self.report({"ERROR"}, "Selected GEO was not found in this archive")
                return {"CANCELLED"}
            if not any(name == selected for name, _ in vehicle_geos(entries)):
                self.report({"ERROR"}, "Selected GEO was not found in its VIV archive")
                return {"CANCELLED"}
            with tempfile.TemporaryDirectory(prefix="nfshs_viv_") as temp:
                for name, payload in entries:
                    if name == selected or name.lower().endswith(".psh"):
                        with open(os.path.join(temp, os.path.basename(name)), "wb") as f:
                            f.write(payload)
                geo_path = os.path.join(temp, os.path.basename(selected))
                status = geo_import.main(context, geo_path, False, self.clear_scene,
                                         default_geo_matrix(), self.mirror_left_right)
            if status == {"CANCELLED"}:
                return status
            coll = bpy.data.collections.get(os.path.basename(selected))
            if coll:
                coll["nfs_viv_source"] = os.path.abspath(archive_path)
                coll["nfs_viv_geo_member"] = selected
                coll["nfs_viv_members"] = json.dumps([n for n, _ in entries])
            self.report({"INFO"}, "Imported %s from VIV" % os.path.basename(selected))
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

    def draw(self, context):
        self.layout.prop(self, "vehicle", text="Vehicle in archive")
        self.layout.prop(self, "clear_scene")
        self.layout.prop(self, "mirror_left_right")


class ExportNFSVIV(Operator, ExportHelper):
    bl_idname = "export_nfshsps1.viv"
    bl_label = "Export edited vehicle to VIV"
    filename_ext = ".viv"
    filter_glob: StringProperty(default="*.viv", options={"HIDDEN"})

    def invoke(self, context, event):
        coll = context.collection
        if coll and coll.get("nfs_viv_source"):
            self.filepath = os.path.splitext(os.path.basename(coll["nfs_viv_source"]))[0] + "_edited.viv"
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        coll = context.collection
        if not coll or not coll.get("nfs_viv_source"):
            self.report({"ERROR"}, "Select the imported vehicle collection first")
            return {"CANCELLED"}
        source = coll["nfs_viv_source"]
        geo_member = coll["nfs_viv_geo_member"]
        try:
            _, entries = read_bigf(source)
            with tempfile.TemporaryDirectory(prefix="nfshs_viv_export_") as temp:
                geo_export.main(context, temp, False, default_geo_matrix())
                exported = os.path.join(temp, coll.name)
                if not os.path.isfile(exported):
                    candidates = [os.path.join(temp, n) for n in os.listdir(temp) if n.lower().endswith(".geo")]
                    if not candidates:
                        raise ValueError("GEO exporter did not produce a file")
                    exported = candidates[0]
                new_geo = open(exported, "rb").read()
            replaced = False
            rebuilt = []
            for name, payload in entries:
                if name == geo_member:
                    payload, replaced = new_geo, True
                elif name.lower().endswith(".psh"):
                    image = bpy.data.images.get(os.path.basename(name))
                    if image is not None and tuple(image.size) == (256, 256):
                        payload = _psh_edited(payload, image)
                rebuilt.append((name, payload))
            if not replaced:
                raise ValueError("Original GEO member not found in source archive")
            output = write_bigf(rebuilt)
            with open(self.filepath, "wb") as f:
                f.write(output)
            self.report({"INFO"}, "Saved edited GEO and PSH texture pages in VIV")
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


def menu_import(self, context):
    self.layout.operator(ImportNFSVIV.bl_idname, text="NFS High Stakes PS1 vehicle archive (.viv)")


def menu_export(self, context):
    self.layout.operator(ExportNFSVIV.bl_idname, text="NFS High Stakes PS1 vehicle archive (.viv)")


CLASSES = (ImportNFSVIV, ExportNFSVIV)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

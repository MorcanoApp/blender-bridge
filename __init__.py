"""Morcano bridge for Blender: send the scene to a Morcano board in one click.

Install: Edit > Preferences > Add-ons > Install from Disk, pick this file,
tick "Morcano bridge". Then View3D > Sidebar (N) > Morcano > Send to Morcano.
"Check for print" first lists what would stop the studio, object by object
(open edges, empty meshes, too many triangles), with how to fix each; Send
runs the same check and stops on a problem.

What goes (and nothing else): the shapes of the visible mesh and text
objects (or the selected ones) as they show - modifiers on - in world space;
their names, material names and colours, parents; each text's words, font
name, size, alignment and place, so the studio can set other words in the
same spot; the .blend's file name as the model's name. A font file goes only
when it is packed in the .blend or "Send fonts" is ticked (a font's licence
may not allow sending it). No paths, no other data blocks, no images, no
scripts, no account or computer details. It goes only to morcano.app over
HTTPS (or to a studio on this computer, for testing).
The board opens in your browser with the model on it; "Create generator"
there makes it a generator.

The same file is the studio's exporter for an uploaded .blend
(tools/blend_export.py runs ``export_scene`` in a headless Blender).
"""

bl_info = {
    "name": "Morcano bridge",
    "author": "Morcano",
    "version": (0, 2, 1),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Morcano",
    "description": "Send the model to a Morcano board and make it a generator",
    "category": "Import-Export",
}

import json
import os
import struct

import bpy

MAX_OBJECTS = 32
MAX_TRIANGLES = 400_000
MAX_FONT_BYTES = 2 * 1024 * 1024
DEFAULT_STUDIO = "https://morcano.app"
#: The only places a scene may go: Morcano, or a studio on this computer.
_HOSTS = ("morcano.app",)
_LOCAL = ("localhost", "127.0.0.1", "::1")
#: Let's Encrypt's ISRG Root X2 (public, valid to 2040; sha256 69:72:9B:8E...:14:70).
#: morcano.app's chain runs up to it; Blender's Python on Windows checks with
#: OpenSSL against the system store, which may hold only the expired cross-sign
#: ("certificate has expired"), so the root goes in alongside the system's.
_ISRG_ROOT_X2 = """-----BEGIN CERTIFICATE-----
MIICGzCCAaGgAwIBAgIQQdKd0XLq7qeAwSxs6S+HUjAKBggqhkjOPQQDAzBPMQsw
CQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJuZXQgU2VjdXJpdHkgUmVzZWFyY2gg
R3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBYMjAeFw0yMDA5MDQwMDAwMDBaFw00
MDA5MTcxNjAwMDBaME8xCzAJBgNVBAYTAlVTMSkwJwYDVQQKEyBJbnRlcm5ldCBT
ZWN1cml0eSBSZXNlYXJjaCBHcm91cDEVMBMGA1UEAxMMSVNSRyBSb290IFgyMHYw
EAYHKoZIzj0CAQYFK4EEACIDYgAEzZvVn4CDCuwJSvMWSj5cz3es3mcFDR0HttwW
+1qLFNvicWDEukWVEYmO6gbf9yoWHKS5xcUy4APgHoIYOIvXRdgKam7mAHf7AlF9
ItgKbppbd9/w+kHsOdx1ymgHDB/qo0IwQDAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0T
AQH/BAUwAwEB/zAdBgNVHQ4EFgQUfEKWrt5LSDv6kviejM9ti6lyN5UwCgYIKoZI
zj0EAwMDaAAwZQIwe3lORlCEwkSHRhtFcP9Ymd70/aTSVaYgLXTWNLxBo1BfASdW
tL4ndQavEi51mI38AjEAi/V3bNTIZargCyzuFJ0nN6T5U6VR5CmD1/iQMVtCnwr1
/q4AaOeMSQ+2b1tbFfLn
-----END CERTIFICATE-----"""


# ------------------------------------------------------------------ export

def _hex(obj):
    """The first material's colour (the node base colour, else the viewport one)."""
    mats = [m for m in getattr(obj.data, "materials", []) if m]
    if not mats:
        return None
    m = mats[0]
    rgba = list(m.diffuse_color)
    if m.use_nodes and m.node_tree:
        for node in m.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                rgba = list(node.inputs["Base Color"].default_value)
                break

    def srgb(c):                         # linear -> sRGB, as Blender shows it
        c = max(0.0, min(1.0, c))
        c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        return round(c * 255)
    return "#" + "".join(f"{srgb(c):02X}" for c in rgba[:3])


def _write_stl(path, obj, depsgraph):
    """The evaluated object (modifiers on, a text as its letters) in world space."""
    ev = obj.evaluated_get(depsgraph)
    mesh = ev.to_mesh()
    try:
        mesh.calc_loop_triangles()
        mw = obj.matrix_world
        verts = [mw @ v.co for v in mesh.vertices]
        tris = mesh.loop_triangles
        with open(path, "wb") as f:
            f.write(b"\0" * 80 + struct.pack("<I", len(tris)))
            for t in tris:
                a, b, c = (verts[i] for i in t.vertices)
                f.write(struct.pack("<12fH", 0, 0, 0, *a, *b, *c, 0))
        return len(tris)
    finally:
        ev.to_mesh_clear()


def _font(curve, out, seen, disk_fonts=False):
    """The font's name, and its file: when packed in the .blend, or - only when
    asked (``disk_fonts``) - the file on this computer."""
    font = curve.font
    if font is None:
        return {"name": None}
    info = {"name": font.name, "packed": bool(font.packed_file)}
    blob = None
    if font.packed_file:
        blob = font.packed_file.data
    elif disk_fonts and font.filepath and font.filepath != "<builtin>":
        path = bpy.path.abspath(font.filepath)
        try:
            if os.path.isfile(path) and os.path.getsize(path) <= MAX_FONT_BYTES:
                with open(path, "rb") as f:
                    blob = f.read()
        except OSError:
            blob = None
    if blob and len(blob) <= MAX_FONT_BYTES:
        if font.name not in seen:
            seen[font.name] = f"font_{len(seen)}.ttf"
            with open(os.path.join(out, seen[font.name]), "wb") as f:
                f.write(blob)
        info["file"] = seen[font.name]
    return info


def _shown(obj):
    """A mesh or text that renders and shows in the viewport."""
    if obj.type not in ("MESH", "FONT") or obj.hide_render:
        return False
    try:
        return obj.visible_get()
    except RuntimeError:                  # not in the view layer
        return False


def export_scene(out, objects=None, disk_fonts=False):
    """Write OUT/manifest.json and one binary STL per object (Blender units,
    world space; ``unit_mm`` makes millimetres). ``objects``: those to send,
    else every mesh and text that shows."""
    os.makedirs(out, exist_ok=True)
    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()
    units = scene.unit_settings
    unit_mm = 1000.0 * (units.scale_length or 1.0) if units.system != "NONE" else 1.0
    pick = list(objects) if objects is not None else list(scene.objects)
    recs, fonts, total = [], {}, 0
    for obj in pick:
        if not _shown(obj):
            continue
        if len(recs) >= MAX_OBJECTS:
            raise RuntimeError(f"more than {MAX_OBJECTS} objects")
        name = f"o_{len(recs)}.stl"
        n = _write_stl(os.path.join(out, name), obj, depsgraph)
        if not n:
            continue
        total += n
        if total > MAX_TRIANGLES:
            raise RuntimeError(f"more than {MAX_TRIANGLES} triangles")
        rec = {"name": obj.name, "type": "text" if obj.type == "FONT" else "mesh",
               "file": name, "triangles": n,
               "parent": obj.parent.name if obj.parent else None,
               "material": (obj.data.materials[0].name
                            if getattr(obj.data, "materials", None) and obj.data.materials[0]
                            else None),
               "colour": _hex(obj)}
        if obj.type == "FONT":
            c = obj.data
            rec["text"] = {
                "body": c.body, "size": c.size, "extrude": c.extrude,
                "bevel_depth": c.bevel_depth, "offset": c.offset,
                "offset_x": c.offset_x, "offset_y": c.offset_y,
                "align_x": c.align_x, "align_y": c.align_y,
                "space_character": c.space_character, "space_line": c.space_line,
                "shear": c.shear,
                "font": _font(c, out, fonts, disk_fonts),
                "matrix": [list(row) for row in obj.matrix_world],
            }
        recs.append(rec)
    if not recs:
        raise RuntimeError("no mesh or text object to send")
    label = os.path.splitext(os.path.basename(bpy.data.filepath))[0] or "Blender model"
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"unit_mm": unit_mm, "unit_system": units.system, "label": label,
                   "objects": recs}, f, ensure_ascii=False, indent=1)
    return recs


# ------------------------------------------------------------------ pre-print check

def check_scene(objects=None):
    """What would stop the studio, said here per object before anything goes:
    ``[(object name, "ERROR"|"INFO", what and how to fix)]``. A part must be a
    closed solid; a flat text is fine (it stands 1 mm high)."""
    import bmesh
    scene = bpy.context.scene
    depsgraph = bpy.context.evaluated_depsgraph_get()
    pick = list(objects) if objects is not None else list(scene.objects)
    out, total = [], 0
    for obj in pick:
        if not _shown(obj):
            continue
        if obj.type == "FONT":
            c = obj.data
            if c.extrude == 0 and c.bevel_depth == 0:
                out.append((obj.name, "INFO", "Flat text - it will stand 1 mm out of its part. "
                                              "Set Geometry > Extrude for your own depth."))
            continue
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            bm = bmesh.new()
            bm.from_mesh(mesh)
            total += sum(len(f.verts) - 2 for f in bm.faces)
            holes = sum(1 for e in bm.edges if not e.is_manifold)
            loose = sum(1 for v in bm.verts if not v.link_faces)
            if not bm.faces:
                out.append((obj.name, "ERROR", "No faces - only points or lines. Delete it or "
                                               "give it a surface."))
            elif holes:
                out.append((obj.name, "ERROR",
                            f"Not closed: {holes} open or shared edges. Edit Mode > Select > "
                            "All by Trait > Non Manifold shows them; Mesh > Clean Up > Fill "
                            "Holes, or a Solidify modifier for a thin surface."))
            elif loose:
                out.append((obj.name, "INFO", f"{loose} loose points - they are dropped."))
            bm.free()
        finally:
            ev.to_mesh_clear()
    if total > MAX_TRIANGLES:
        out.append(("Scene", "ERROR", f"{total:,} triangles - over {MAX_TRIANGLES:,}. "
                                      "Decimate the heaviest objects."))
    return out


# ------------------------------------------------------------------ the bridge

def _multipart(fields, files):
    import uuid
    boundary = uuid.uuid4().hex
    body = bytearray()
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                 f"{v}\r\n").encode()
    for k, (fname, data) in files.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                 f"filename=\"{fname}\"\r\nContent-Type: application/zip\r\n\r\n").encode()
        body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def studio_url(studio):
    """The studio's address, if the scene may go there: https://morcano.app
    (or a subdomain), or a studio on this computer. Else None."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit((studio or "").strip())
        host = (u.hostname or "").lower()
        port = u.port
    except ValueError:
        return None
    if u.username or u.password or u.query or u.fragment or u.path not in ("", "/"):
        return None
    if host in _LOCAL and u.scheme in ("http", "https"):
        shown = "[::1]" if host == "::1" else host
        return f"{u.scheme}://{shown}" + (f":{port}" if port else "")
    if u.scheme == "https" and port in (None, 443) and \
            any(host == h or host.endswith("." + h) for h in _HOSTS):
        return f"https://{host}"
    return None


def _tls():
    """The system's trusted roots and ISRG Root X2 (see above); still verified."""
    import ssl
    ctx = ssl.create_default_context()
    try:
        ctx.load_verify_locations(cadata=_ISRG_ROOT_X2)
    except ssl.SSLError:
        pass
    return ctx


def send(studio, only_selected, disk_fonts=False):
    """Export, zip, post to the studio; the board link to open."""
    import io
    import re
    import tempfile
    import urllib.error
    import urllib.request
    import zipfile
    base = studio_url(studio)
    if base is None:
        raise RuntimeError("The add-on sends only to https://morcano.app (or a studio on "
                           "this computer for testing) - check the address.")
    objs = list(bpy.context.selected_objects) if only_selected else None
    with tempfile.TemporaryDirectory(prefix="morcano_") as tmp:
        recs = export_scene(tmp, objs, disk_fonts)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in sorted(os.listdir(tmp)):
                z.write(os.path.join(tmp, name), name)
    body, ctype = _multipart({"version": ".".join(map(str, bl_info["version"]))},
                             {"file": ("scene.zip", buf.getvalue())})
    url = base + "/api/bridge/upload"
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": ctype,
                                          "User-Agent": "MorcanoBridge/" + ".".join(
                                              map(str, bl_info["version"]))})
    try:
        with urllib.request.urlopen(req, timeout=120, context=_tls()) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
        except Exception:
            data = {"error": f"The studio answered {exc.code}."}
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"Could not reach {base}: {exc}") from None
    if not data.get("ok"):
        raise RuntimeError(str(data.get("error") or "The studio did not take the model.")[:300])
    link = str(data.get("open") or "")
    if not re.fullmatch(r"/board/new\?import=[0-9a-f]{20}", link):
        raise RuntimeError("The studio's answer was not a board link.")
    return base + link, len(recs)


def _prefs(context):
    addon = context.preferences.addons.get(__package__ or __name__)
    return addon.preferences if addon else None


class MorcanoBridgePrefs(bpy.types.AddonPreferences):
    bl_idname = __package__ or __name__

    studio: bpy.props.StringProperty(
        name="Studio address", default=DEFAULT_STUDIO,
        description="https://morcano.app - or a studio on this computer "
                    "(http://localhost:PORT) for testing")
    disk_fonts: bpy.props.BoolProperty(
        name="Send fonts", default=False,
        description="Send the font files your texts use, so the board draws the same "
                    "letters. Off: a packed font still goes; others are drawn in Noto Sans. "
                    "Tick only for fonts whose licence allows it")

    def draw(self, context):
        self.layout.prop(self, "studio")
        self.layout.prop(self, "disk_fonts")


class MORCANO_OT_send(bpy.types.Operator):
    """Send the model to a Morcano board (opens in your browser)"""
    bl_idname = "morcano.send"
    bl_label = "Send to Morcano"

    only_selected: bpy.props.BoolProperty(name="Only selected", default=False)

    def execute(self, context):
        import webbrowser
        prefs = _prefs(context)
        studio = prefs.studio if prefs else DEFAULT_STUDIO
        disk_fonts = bool(prefs.disk_fonts) if prefs else False
        objs = list(context.selected_objects) if self.only_selected else None
        found = check_scene(objs)
        _keep_check(context, found)
        bad = [f for f in found if f[1] == "ERROR"]
        if bad:
            self.report({"ERROR"}, f"{bad[0][0]}: {bad[0][2]}"
                        + (f" (+{len(bad) - 1} more in the Morcano panel)" if len(bad) > 1 else ""))
            return {"CANCELLED"}
        try:
            link, n = send(studio, self.only_selected, disk_fonts)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        webbrowser.open(link)
        self.report({"INFO"}, f"Sent {n} object{'s' if n != 1 else ''} - the board is "
                              "opening in your browser.")
        return {"FINISHED"}


def _keep_check(context, found):
    context.window_manager["morcano_check"] = json.dumps(found)


class MORCANO_OT_check(bpy.types.Operator):
    """Check every part prints: closed solids, texts, size"""
    bl_idname = "morcano.check"
    bl_label = "Check for print"

    def execute(self, context):
        found = check_scene()
        _keep_check(context, found)
        bad = sum(1 for f in found if f[1] == "ERROR")
        self.report({"ERROR"} if bad else {"INFO"},
                    f"{bad} to fix before sending." if bad else "Ready to send.")
        return {"FINISHED"}


class MORCANO_OT_pick(bpy.types.Operator):
    """Select this object"""
    bl_idname = "morcano.pick"
    bl_label = "Select"

    name: bpy.props.StringProperty()

    def execute(self, context):
        obj = bpy.data.objects.get(self.name)
        if obj is None:
            return {"CANCELLED"}
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


class MORCANO_PT_panel(bpy.types.Panel):
    bl_label = "Morcano"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Morcano"

    def draw(self, context):
        col = self.layout.column(align=True)
        col.operator(MORCANO_OT_send.bl_idname, text="Send to Morcano",
                     icon="EXPORT").only_selected = False
        col.operator(MORCANO_OT_send.bl_idname, text="Send selected",
                     icon="RESTRICT_SELECT_OFF").only_selected = True
        prefs = _prefs(context)
        if prefs:
            self.layout.prop(prefs, "studio", text="")
            self.layout.prop(prefs, "disk_fonts")
        self.layout.operator(MORCANO_OT_check.bl_idname, icon="CHECKMARK")
        raw = context.window_manager.get("morcano_check")
        if raw is not None:
            found = json.loads(raw)
            box = self.layout.box()
            if not found:
                box.label(text="Ready to send.", icon="CHECKMARK")
            for name, level, text in found[:12]:
                row = box.row()
                row.label(text=name, icon="ERROR" if level == "ERROR" else "INFO")
                if name in bpy.data.objects:
                    row.operator(MORCANO_OT_pick.bl_idname, text="", icon="RESTRICT_SELECT_OFF").name = name
                col = box.column(align=True)
                for line in _wrap(text, 38):
                    col.label(text=line)
        self.layout.label(text="Sends shapes, colours and texts only.")


def _wrap(text, width):
    import textwrap
    return textwrap.wrap(text, width) or [""]


_CLASSES = (MorcanoBridgePrefs, MORCANO_OT_send, MORCANO_OT_check, MORCANO_OT_pick,
            MORCANO_PT_panel)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)

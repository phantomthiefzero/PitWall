"""Camera packs for iRacing: install the free ones a user downloads.

Camera packs are track and car camera files shared by the community. The free
ones found (BIG EVIL Racing, RedneckTeck, forum and Discord sets) do not allow
redistribution, so the suite does not ship any: it installs a pack the user
downloaded, into Documents\\iRacing\\cameras, where iRacing looks for them.
Once loaded (Ctrl+F12 > Load track / Load car, or automatically), their groups
appear in the session's camera list, and the producer panel and auto-director
use them like any other.
"""
import os
import zipfile


def documents_dir() -> str:
    """The user's Documents folder, following a OneDrive redirection on Windows."""
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:  # CSIDL_PERSONAL
            return buf.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def cameras_dir() -> str:
    return os.path.join(documents_dir(), "iRacing", "cameras")


def install_pack(zip_path: str, dest: str | None = None) -> dict:
    """Copy a pack's tracks/ and cars/ folders into iRacing's cameras folder.

    Packs are zipped either as tracks/<track>/..., cars/<car>/..., or with one
    folder above that. Anything outside tracks/ and cars/, and any path that
    would leave the destination, is skipped. Returns what was installed."""
    dest = os.path.realpath(dest or cameras_dir())
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        root = None
        for n in names:
            parts = n.replace("\\", "/").split("/")
            for i, p in enumerate(parts[:-1]):
                if p.lower() in ("tracks", "cars"):
                    root = i if root is None else min(root, i)
                    break
        if root is None:
            return {"ok": False, "installed": [], "error":
                    "This zip has no tracks or cars folder. Unzip it and copy its files into "
                    + dest + " following the pack's instructions."}
        installed, skipped = [], 0
        for n in names:
            parts = n.replace("\\", "/").split("/")[root:]
            if len(parts) < 3 or parts[0].lower() not in ("tracks", "cars") or any(p in ("", ".", "..") for p in parts):
                skipped += 1
                continue
            target = os.path.realpath(os.path.join(dest, parts[0].lower(), *parts[1:]))
            if not target.startswith(dest + os.sep):
                skipped += 1
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(n) as src, open(target, "wb") as out:
                out.write(src.read())
            installed.append("/".join([parts[0].lower()] + parts[1:]))
    folders = sorted({"/".join(p.split("/")[:2]) for p in installed})
    return {"ok": bool(installed), "installed": installed, "folders": folders, "skipped": skipped, "dest": dest,
            "error": None if installed else "Nothing to install in that zip."}

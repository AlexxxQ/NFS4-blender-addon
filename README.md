# NFS4 Blender Add-on

Need for Speed High Stakes PS1 GEO/VIV importer and exporter, updated for Blender 5.1.2.

## Install

Install `NFS_HS_PS1_IO_Blender_5.1.2_VIV_tools.zip` through **Edit > Preferences > Add-ons > Install from Disk**, then enable the add-on.

## Features

- Import and export GEO models, with PSH texture-page atlases.
- Browse vehicles in all VIV archives in a selected DATA folder and import the chosen vehicle.
- Export edited GEO and texture pages back into a VIV archive.
- Decode PS1 transparent and semi-transparent CLUT pixels. Materials 4 and 13 use Dithered rendering with the PSH alpha output connected directly to Principled BSDF Alpha.
- Optional left/right mirroring for models whose steering side is reversed in the source data.

The add-on source is in `nfs_hs_ps1_io/`.
<img width="800" height="608" alt="Снимок экрана 2026-11-25 193641" src="https://github.com/user-attachments/assets/86614ee0-2870-4bde-af73-eb124f86392f" />

## PS1 track geometry import (develop)

Use **File > Import > NFS High Stakes PS1 Track (.grp)** and select the track's base GRP file in the game's `DATA` folder, for example `ZTR03.GRP`. The matching PSH file is already in that same folder; for a night variant such as `ZTR00N.GRP`, the texture file is `ZTR00N0.PSH`.

The importer builds one Blender object per track chunk, applies the PS1-to-Blender axis conversion, and defaults to 0.01 scale. It decodes the matching PSH page into a 4096x768 atlas and assigns material UVs from the shape and material tables. Track export, placed roadside object instances, and sky/horizon import are not implemented yet.

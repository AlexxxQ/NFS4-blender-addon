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

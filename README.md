# NFS4 Blender Add-on

Need for Speed High Stakes PS1 vehicle and track tools, updated for Blender 5.1.2.

## Install

The add-on package is in `nfs_hs_ps1_io/`. To install it, create a ZIP with that folder at the archive root, then select it in **Edit > Preferences > Add-ons > Install from Disk** and enable **Need for Speed High Stakes PS1 GEO/VIV Import/Export**.

## Vehicle features

- Import and export GEO models with PSH texture-page atlases.
- Browse vehicles across VIV archives in a selected DATA folder.
- Export edited GEO and texture pages back into a VIV archive.
- Decode PS1 transparent and semi-transparent CLUT pixels.
- Use dithered rendering with the PSH alpha output connected directly to Principled BSDF Alpha for vehicle materials 4 and 13.

## Track import

Choose **File > Import > NFS High Stakes PS1 Track (.grp)** and select the base track GRP in the game's DATA folder, such as `ZTR03.GRP`. Keep the matching PSH beside it; for a night track such as `ZTR00N.GRP`, the texture page is `ZTR00N0.PSH`.

The importer creates chunk geometry and, by default, imports track-owned static objects such as signs, lamps, houses, and roadside props. It groups statics into chunk, persistent, and midground collections, reuses linked meshes, and applies the same scale to roads, static geometry, and instance positions. Material UVs follow the game's shape and UV-flag tables.

Camera-facing billboards and animated objects are imported in a fixed pose. Sky/horizon import and track export are not implemented.

The add-on source is in `nfs_hs_ps1_io/`.

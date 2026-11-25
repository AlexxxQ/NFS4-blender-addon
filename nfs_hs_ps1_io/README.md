# Need for Speed High Stakes PS1 GEO Import/Export

Compatibility package prepared for Blender 5.1.2. It includes the original GEO importer, exporter, facet-flag panel, icon, and license.

## Install

1. In Blender, open **Edit > Preferences > Add-ons**.
2. Choose **Install from Disk** and select `NFS_HS_PS1_IO_Blender_5.1.2_track_import.zip`.
3. Enable **Need for Speed High Stakes PS1 GEO Import/Export** in the add-on list.

The import and export commands are available from **File > Import** and **File > Export**. Import `.geo` files extracted from the game archives. To load textures automatically, keep the vehicle's companion `.psh` files in the same folder as its `.geo`; the importer decodes the PS1 pages and links images to the material IDs used by the model. The importer also has a **Mirror left/right** option, enabled by default, for GEOs whose steering wheel and driver are on the opposite side.

### PS1 track geometry and static-object import

Use **File > Import > NFS High Stakes PS1 Track (.grp)** and select the base track geometry file, for example `ZTR03.GRP` from the game's `DATA` folder. Keep its matching PSH in the same folder; for night variants such as `ZTR00N.GRP`, the texture file is `ZTR00N0.PSH`. The importer decodes the PSH page into a 4096x768 atlas and maps track and static-object materials to their source shapes.

The default scale is 0.01 and applies consistently to road geometry, static mesh coordinates, and instance positions. **Import Static Objects** is enabled by default and imports the track's shared object definitions and their placements, including chunk props, persistent statics, and midground statics. The objects are grouped into separate collections and use linked meshes for repeated instances. Camera-facing billboards and animated/sign objects are imported in a fixed pose; their in-game camera behavior and animation are not reproduced. Special-condition instance groups are imported into a separate collection when present. Sky/horizon import and track export are not implemented.

### VIV vehicle workflow

- **File > Import > NFS High Stakes PS1 vehicle archive (.viv)** opens a VIV picker, then a second dialog listing vehicles from every VIV in the selected folder. Choose a vehicle to open its matching archive and import the GEO; duplicate vehicle labels include the archive name. Companion PSH pages are unpacked temporarily and imported as texture atlases. The **Color** setting selects one of the game's 16 palette slots (shown as Color 1–16 because the game data has no names); the first and second groups of eight use the vehicle's L and D palette banks.
- Select the imported vehicle collection and use **File > Export > NFS High Stakes PS1 vehicle archive (.viv)**. The add-on exports the edited GEO and repacks the archive. It updates matching 256x256 PSH image datablocks with the edited atlas colors, quantizing them to the original 4/8/16-bit page format, and retains unrelated archive members.
- Export to a new VIV file. The input archive is not overwritten.

Texture editing is page-based: keep each PSH image at 256x256 and retain its image datablock name so it can be matched to the original archive member. The exporter writes colors into the existing PSH page shapes and palettes; shape dimensions and UV layout remain fixed.

## Compatibility changes

- Added a package entry point so Blender can install and enable the importer and exporter together.
- Set the minimum Blender version to 3.6.0.
- Replaced removed split-normal calculation/free calls with mesh updates while retaining custom-normal import.
- Updated the facet-flag panel to use Blender mesh attributes.
- Updated the viewport framing call for Blender 5 context overrides.
- Added companion SHPP/PSH texture-page decoding and material image links.
- Decoded PS1 alpha from zero-valued CLUT colors, matching transparent VRAM texels.
- Decoded PS1 STP bit 15 as per-pixel 50% alpha and retained it when writing edited PSH pages.
- Applies PS1 STP alpha only where both the texel and the GEO face enable semi-transparency; this keeps shared roof/window texture pages opaque on roof faces.
- Uses Blender's blended surface method only for material IDs with GEO faces that enable STP transparency.
- Uses dithered rendering with a direct PSH alpha link for vehicle material IDs 4 and 13.
- Added an optional left/right mirror that reverses face winding and UV corners with the geometry.
- Added a VIV import color selector that applies the selected car palette bank and row to indexed vehicle textures.

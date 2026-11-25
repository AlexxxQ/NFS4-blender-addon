"""Blender add-on for Need for Speed High Stakes PS1 GEO files."""

bl_info = {
    "name": "Need for Speed High Stakes PS1 GEO/VIV Import/Export",
    "author": "PolySoupList, DGIorio; Blender 5 compatibility updates",
    "version": (1, 3, 1),
    "blender": (3, 6, 0),
    "location": "File > Import/Export",
    "description": "Import/export NFS High Stakes PS1 vehicles and import track geometry",
    "category": "Import-Export",
}

from . import create_flag_data_panel
from . import export_nfshs_ps1_models
from . import import_nfshs_ps1_models
from . import import_nfshs_ps1_tracks
from . import viv_workflow


def register():
    import_nfshs_ps1_models.register()
    import_nfshs_ps1_tracks.register()
    export_nfshs_ps1_models.register()
    create_flag_data_panel.register()
    viv_workflow.register()


def unregister():
    viv_workflow.unregister()
    create_flag_data_panel.unregister()
    export_nfshs_ps1_models.unregister()
    import_nfshs_ps1_tracks.unregister()
    import_nfshs_ps1_models.unregister()


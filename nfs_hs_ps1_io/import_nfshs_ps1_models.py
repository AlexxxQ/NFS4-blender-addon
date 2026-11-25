#-*- coding:utf-8 -*-

# Blender Need for Speed High Stakes (1999) PS1 importer Add-on
# Add-on developed by PolySoupList with contributions by DGIorio


bl_info = {
	"name": "Import Need for Speed High Stakes (1999) PS1 models format (.geo)",
	"description": "Import meshes files from Need for Speed High Stakes (1999) PS1",
	"author": "PolySoupList",
	"version": (1, 0, 0),
	"blender": (3, 6, 0),
	"location": "File > Import > Need for Speed High Stakes (1999) PS1 (.geo)",
	"warning": "",
	"wiki_url": "",
	"tracker_url": "",
	"support": "COMMUNITY",
	"category": "Import-Export"}


import bpy
from bpy.types import (
	Operator,
	OperatorFileListElement
)
from bpy.props import (
	StringProperty,
	BoolProperty,
	CollectionProperty
)
from bpy_extras.io_utils import (
	ImportHelper,
	orientation_helper,
	axis_conversion,
)
import bmesh
import math
from mathutils import Matrix
import os
import time
import struct


# Global variables
TRANSLATION_SCALE = 65536
VERTEX_SCALE = 256
NVERTEX_SCALE = 4096

CAR_TEXTURE_NAMES = (
	"frnt", "rear", "side", "top1", "top2", "dHiF", "dHiR", "dHiS", "dTp1", "dTop",
	"dLoF", "dLoR", "dLoS", "wndw", "ftdm", "splr", "ext2", "ext3", "ext4", "whl ",
	"ext1", "botm", "whlI", "tred", "bROf", "bSOf", "rvOf", "sFOf", "sROf", "ltOf",
	"bROn", "bSOn", "shdw", "sFOn", "sROn", "ltOn", "dcl1", "dcl2", "dcl3", "dcl4",
	"dcl5", "dcl6", "ins0", "ins1", "ins2", "ins3", "ins4", "ins5", "ins6", "ins7", "ins8",
)

ACTIVE_CAR_TEXTURES = {}
ACTIVE_CAR_STP_TEXTURES = set()
ACTIVE_CAR_SEMITRANS_TEXTURES = set()


def read_psh_atlas(file_path):
	"""Decode a PS1 SHPP texture file into its 256x256 VRAM layout."""
	with open(file_path, "rb") as file:
		data = file.read()
	if len(data) < 16 or data[:4] != b"SHPP":
		return None, set()

	shape_count = struct.unpack_from("<I", data, 8)[0]
	if shape_count > 4096 or 16 + shape_count * 8 > len(data):
		return None, set()

	width = height = 256
	pixels = [0.0] * (width * height * 4)
	shape_names = set()
	stp_shape_names = set()
	for index in range(shape_count):
		entry = 16 + index * 8
		name = data[entry:entry + 4].decode("ascii", errors="ignore").strip(" \0").lower()
		offset = struct.unpack_from("<I", data, entry + 4)[0]
		if not name or offset + 16 > len(data):
			continue
		shape_names.add(name)
		kind = data[offset]
		depth_code = kind & 3
		if depth_code not in (0, 1, 2):
			continue
		shape_width, shape_height = struct.unpack_from("<hh", data, offset + 4)
		if shape_width <= 1 or shape_height <= 1 or shape_width > 1024 or shape_height > 512:
			continue
		position = struct.unpack_from("<I", data, offset + 12)[0]
		x_words = position & 0xFFF
		y = (position >> 16) & 0xFFF
		depth = (4, 8, 16)[depth_code]
		x = x_words * (4 if depth == 4 else 2 if depth == 8 else 1)
		row_bytes = ((shape_width * depth + 15) & ~15) // 8
		pixel_offset = offset + 16
		if pixel_offset + row_bytes * shape_height > len(data):
			continue

		palette = None
		if depth < 16:
			next_offset = data[offset + 1] | (data[offset + 2] << 8) | (data[offset + 3] << 16)
			palette_offset = offset + next_offset + 16
			palette_size = 16 if depth == 4 else 256
			if next_offset < 16 or palette_offset + palette_size * 2 > len(data):
				continue
			palette = []
			for color_index in range(palette_size):
				word = struct.unpack_from("<H", data, palette_offset + color_index * 2)[0]
				r = (word & 31) / 31.0
				g = ((word >> 5) & 31) / 31.0
				b = ((word >> 10) & 31) / 31.0
				# CLUT 0 is transparent; bit 15 marks per-pixel PS1 semi-transparency.
				alpha = 0.0 if word == 0 else (0.5 if word & 0x8000 else 1.0)
				palette.append((r, g, b, alpha))

		shape_has_stp = False
		for py in range(shape_height):
			atlas_y = y + py
			if atlas_y >= height:
				break
			row_offset = pixel_offset + py * row_bytes
			for px in range(shape_width):
				atlas_x = x + px
				if atlas_x >= width:
					break
				if depth == 4:
					packed = data[row_offset + px // 2]
					color_index = (packed & 15) if (px & 1) == 0 else (packed >> 4)
					color = palette[color_index]
				elif depth == 8:
					color_index = data[row_offset + px]
					color = palette[color_index]
				else:
					word = struct.unpack_from("<H", data, row_offset + px * 2)[0]
					alpha = 0.0 if word == 0 else (0.5 if word & 0x8000 else 1.0)
					color = ((word & 31) / 31.0, ((word >> 5) & 31) / 31.0, ((word >> 10) & 31) / 31.0, alpha)
				if color[3] == 0.5:
					shape_has_stp = True
				# Blender image buffers start at the bottom-left; PS1 texture rows start at the top.
				pixel_index = ((height - 1 - atlas_y) * width + atlas_x) * 4
				pixels[pixel_index:pixel_index + 4] = color
		if shape_has_stp:
			stp_shape_names.add(name)

	image = bpy.data.images.new(os.path.basename(file_path), width=width, height=height, alpha=True)
	image.pixels.foreach_set(pixels)
	image.update()
	image["nfs_ps1_stp_shapes"] = "|".join(sorted(stp_shape_names))
	image.pack()
	return image, shape_names


def load_car_textures(geo_path):
	"""Find companion PSH files and assign their atlases to GEO texture IDs."""
	global ACTIVE_CAR_TEXTURES
	global ACTIVE_CAR_STP_TEXTURES
	ACTIVE_CAR_TEXTURES = {}
	ACTIVE_CAR_STP_TEXTURES = set()
	texture_sources = []
	try:
		for filename in os.listdir(os.path.dirname(os.path.abspath(geo_path))):
			if filename.lower().endswith(".psh"):
				path = os.path.join(os.path.dirname(os.path.abspath(geo_path)), filename)
				image, shape_names = read_psh_atlas(path)
				if image is not None:
					stem = os.path.splitext(filename)[0].lower()
					texture_sources.append((stem[-1:] if stem else "", image, shape_names))
	except OSError as error:
		print("Could not scan companion PSH textures: %s" % error)

	for texture_index, shape_name in enumerate(CAR_TEXTURE_NAMES):
		name = shape_name.strip().lower()
		if name == "dtp1":
			name = "dtop"
		if texture_index <= 18:
			priority = ("l", "d", "h", "i", "g")
		elif texture_index <= 40:
			priority = ("h", "g", "i", "l", "d")
		elif texture_index == 42:
			priority = ("h", "i", "g", "l", "d")
		else:
			priority = ("i", "h", "g", "l", "d")
		for suffix in priority:
			match = next((image for source_suffix, image, names in texture_sources if source_suffix == suffix and name in names), None)
			if match is not None:
				ACTIVE_CAR_TEXTURES[texture_index] = match
				if name in match.get("nfs_ps1_stp_shapes", "").split("|"):
					ACTIVE_CAR_STP_TEXTURES.add(texture_index)
				break
	if ACTIVE_CAR_TEXTURES:
		print("Loaded companion PSH textures for %d GEO material IDs" % len(ACTIVE_CAR_TEXTURES))
	else:
		print("No companion PSH textures found beside GEO file")


def mirror_geo_left_right(objects):
	"""Mirror GEO geometry to the opposite driver side and preserve outward normals."""
	for obj in objects:
		translation = obj[2]
		translation[0] = -translation[0]
		vertices = obj[6]
		obj[6] = [(-vertex[0], vertex[1], vertex[2]) for vertex in vertices]
		normals = obj[7]
		obj[7] = [(-normal[0], normal[1], normal[2]) for normal in normals]
		for face in obj[8]:
			face[3], face[4] = face[4], face[3]
			face[6], face[7] = face[7], face[6]


def main(context, file_path, is_traffic, clear_scene, global_matrix, mirror_left_right=True):
	if bpy.ops.object.mode_set.poll():
		bpy.ops.object.mode_set(mode='OBJECT')
	
	if clear_scene == True:
		print("Clearing scene...")
		clearScene(context)
	
	status = import_nfshs_ps1_models(context, file_path, is_traffic, clear_scene, global_matrix, mirror_left_right)
	
	return status


def import_nfshs_ps1_models(context, file_path, is_traffic, clear_scene, m, mirror_left_right=True):
	global ACTIVE_CAR_SEMITRANS_TEXTURES
	start_time = time.time()
	load_car_textures(file_path)
	
	main_collection_name = os.path.basename(file_path)
	main_collection = bpy.data.collections.new(main_collection_name)
	bpy.context.scene.collection.children.link(main_collection)
	
	print("Importing file %s" % os.path.basename(file_path))
	
	## PARSING FILES
	print("Parsing file...")
	parsing_time = time.time()
	
	Transformer_zScene = read_Transformer_zScene(file_path, is_traffic)
	header_unk0, header_unk1, Transformer_zObjects = Transformer_zScene
	ACTIVE_CAR_SEMITRANS_TEXTURES = {
		face[1]
		for obj_data in Transformer_zObjects
		for face in obj_data[8]
		if (face[0] & 1) and face[1] in ACTIVE_CAR_STP_TEXTURES
	}
	if mirror_left_right:
		mirror_geo_left_right(Transformer_zObjects)
	
	elapsed_time = time.time() - parsing_time
	print("... %.4fs" % elapsed_time)
	
	## IMPORTING TO SCENE
	print("Importing data to scene...")
	importing_time = time.time()
	
	main_collection["header_unk0"] = [int_to_id(i) for i in header_unk0]
	main_collection["header_unk1"] = [int_to_id(i) for i in header_unk1]
	
	for index in range(0, len(Transformer_zObjects)):
		Transformer_zObj = Transformer_zObjects[index]
		numVertex, numFacet, translation, object_unk0, object_unk1, object_unk2, vertices, normals, faces = Transformer_zObj
		
		if len(vertices) > 0:
			obj = create_object(index, vertices, normals, faces)
			obj["object_index"] = index
			obj["object_unk0"] = int_to_id(object_unk0)
			obj["object_unk1"] = int_to_id(object_unk1)
			obj["object_unk2"] = int_to_id(object_unk2)
			main_collection.objects.link(obj)
			obj.matrix_world = m @ Matrix.Translation(translation)
	
	elapsed_time = time.time() - importing_time
	print("... %.4fs" % elapsed_time)
	
	## Adjusting scene
	for window in bpy.context.window_manager.windows:
		for area in window.screen.areas:
			if area.type == 'VIEW_3D':
				for space in area.spaces:
					if space.type == 'VIEW_3D':
						space.shading.type = 'MATERIAL'
				region = next(region for region in area.regions if region.type == 'WINDOW')
				with bpy.context.temp_override(window=window, area=area, region=region):
					bpy.ops.view3d.view_all(use_all_regions=False, center=False)
	
	print("Finished")
	elapsed_time = time.time() - start_time
	print("Elapsed time: %.4fs" % elapsed_time)
	return {'FINISHED'}


def read_Transformer_zUV(f):
	u = struct.unpack('<B', f.read(0x1))[0]
	v = struct.unpack('<B', f.read(0x1))[0]
	
	Transformer_zUV = [u, v]
	
	return Transformer_zUV


def read_Transformer_zFacet(f):
	flag = struct.unpack('<h', f.read(0x2))[0]
	textureIndex = struct.unpack('<B', f.read(0x1))[0]
	vertexId0 = struct.unpack('<B', f.read(0x1))[0]
	vertexId1 = struct.unpack('<B', f.read(0x1))[0]
	vertexId2 = struct.unpack('<B', f.read(0x1))[0]
	uv0 = read_Transformer_zUV(f)
	uv1 = read_Transformer_zUV(f)
	uv2 = read_Transformer_zUV(f)
	
	Transformer_zFacet = [flag, textureIndex, vertexId0, vertexId1, vertexId2, uv0, uv1, uv2]
	
	return Transformer_zFacet


def read_Transformer_zObj(f, is_traffic, index):
	vertices = []
	normals = []
	faces = []
	
	numVertex = struct.unpack('<H', f.read(0x2))[0]
	numFacet = struct.unpack('<H', f.read(0x2))[0]
	
	translation = struct.unpack('<3i', f.read(0xC))
	translation = [translation[0]/TRANSLATION_SCALE, translation[1]/TRANSLATION_SCALE, translation[2]/TRANSLATION_SCALE]
	
	if index == 39:
		translation[0] -= 0x7AE/TRANSLATION_SCALE
	elif index == 40:
		translation[0] += 0x7AE/TRANSLATION_SCALE
	
	unk0 = struct.unpack('<I', f.read(0x4))[0]
	unk1 = struct.unpack('<I', f.read(0x4))[0]
	unk2 = struct.unpack('<I', f.read(0x4))[0]
	
	for i in range(numVertex):
		vertex = struct.unpack('<3h', f.read(0x6))
		vertex = [vertex[0]/VERTEX_SCALE, vertex[1]/VERTEX_SCALE, vertex[2]/VERTEX_SCALE]
		vertices.append((vertex[0], vertex[1], vertex[2]))
	if numVertex % 2 == 1:	#Data offset, happens when numVertex is odd
		padding = f.read(0x2)
	
	if is_traffic == False:
		if get_R3DCar_ObjectInfo(index)[1] & 1 != 0:
			for i in range(numVertex):
				Nvertex = struct.unpack('<3h', f.read(0x6))
				Nvertex = [Nvertex[0]/NVERTEX_SCALE, Nvertex[1]/NVERTEX_SCALE, Nvertex[2]/NVERTEX_SCALE]
				normals.append((Nvertex[0], Nvertex[1], Nvertex[2]))
			if numVertex % 2 == 1:	#Data offset, happens when numVertex is odd
				padding = f.read(0x2)
	
	for i in range(numFacet):
		Transformer_zFacet = read_Transformer_zFacet(f)
		faces.append(Transformer_zFacet)
	
	Transformer_zObj = [numVertex, numFacet, translation, unk0, unk1, unk2, vertices, normals, faces]
	
	return Transformer_zObj


def read_Transformer_zScene(file_path, is_traffic):
	Transformer_zObjects = []
	
	with open(file_path, "rb") as f:
		unk0 = struct.unpack('<57I', f.read(0xE4))
		unk1 = struct.unpack('<90I', f.read(0x168))
		
		for index in range(57):
			Transformer_zObj = read_Transformer_zObj(f, is_traffic, index)
			Transformer_zObjects.append(Transformer_zObj)
	
	Transformer_zScene = [unk0, unk1, Transformer_zObjects]
	
	return Transformer_zScene


def create_object(index, vertices, normals, faces):
	geoPartName = get_geoPartNames(index)
	
	#==================================================================================================
	#Building Mesh
	#==================================================================================================
	me_ob = bpy.data.meshes.new(geoPartName)
	obj = bpy.data.objects.new(geoPartName, me_ob)
	
	#Get a BMesh representation
	bm = bmesh.new()
	
	#Creating new properties
	flag = (bm.faces.layers.int.get("flag") or bm.faces.layers.int.new('flag'))
	
	BMVert_dictionary = {}
	
	normal_data = []
	has_some_normal_data = False
	
	uvName = "UVMap" #or UV1Map
	uv_layer = bm.loops.layers.uv.get(uvName) or bm.loops.layers.uv.new(uvName)
	
	for i, position in enumerate(vertices):
		BMVert = bm.verts.new(position)
		BMVert.index = i
		BMVert_dictionary[i] = BMVert
		
		if normals:
			normal = normals[i]
			BMVert.normal = normal
			normal_data.append([i, normal])
			has_some_normal_data = True
		else:
			normal_data.append([i, (0.0, 0.0, 0.0)])
	
	for i, face in enumerate(faces):
		_flag, textureIndex, vertexId0, vertexId1, vertexId2, uv0, uv1, uv2 = face
		
		face_vertices = [BMVert_dictionary[vertexId0], BMVert_dictionary[vertexId1], BMVert_dictionary[vertexId2]]
		face_uvs = [[uv0[0]/0xFF, 1.0 - uv0[1]/0xFF], [uv1[0]/0xFF, 1.0 - uv1[1]/0xFF], [uv2[0]/0xFF, 1.0 - uv2[1]/0xFF]]
		try:
			BMFace = bm.faces.get(face_vertices) or bm.faces.new(face_vertices)
		except:
			pass
		if BMFace.index != -1:
			BMFace0 = BMFace
			BMFace = BMFace.copy(verts=False, edges=False)
			
			original_face_indices = [vert.index for vert in BMFace.verts]
			new_face_indices = [vert.index for vert in face_vertices]
			same_winding_faces_as_original = [original_face_indices[-n:] + original_face_indices[:-n] for n in range(0, len(original_face_indices))]
			if new_face_indices not in same_winding_faces_as_original:
				BMFace.normal_flip()
		
		BMFace.index = i
		BMFace.smooth = True
		BMFace[flag] = _flag
		
		material_name = str(textureIndex)
		mat = bpy.data.materials.get(material_name)
		if mat == None:
			mat = bpy.data.materials.new(material_name)
			mat.use_nodes = True
			mat.name = material_name
			
			if mat.node_tree.nodes[0].bl_idname != "ShaderNodeOutputMaterial":
				mat.node_tree.nodes[0].name = material_name
		image = ACTIVE_CAR_TEXTURES.get(textureIndex)
		if image is not None:
			nodes = mat.node_tree.nodes
			shader = next((node for node in nodes if node.type == 'BSDF_PRINCIPLED'), None)
			if shader is None:
				shader = nodes.new('ShaderNodeBsdfPrincipled')
			output = next((node for node in nodes if node.type == 'OUTPUT_MATERIAL'), None)
			if output is None:
				output = nodes.new('ShaderNodeOutputMaterial')
			texture_node = next((node for node in nodes if node.type == 'TEX_IMAGE' and node.label == "NFS PS1 texture page"), None)
			if texture_node is None:
				texture_node = nodes.new('ShaderNodeTexImage')
			texture_node.label = "NFS PS1 texture page"
			texture_node.image = image
			texture_node.interpolation = 'Closest'
			if not any(link.from_node == texture_node and link.to_socket == shader.inputs['Base Color'] for link in mat.node_tree.links):
				mat.node_tree.links.new(texture_node.outputs['Color'], shader.inputs['Base Color'])
			if 'Alpha' in shader.inputs:
				alpha_input = shader.inputs['Alpha']
				for link in list(mat.node_tree.links):
					if link.to_socket == alpha_input:
						mat.node_tree.links.remove(link)
				if textureIndex in (4, 13) or textureIndex not in ACTIVE_CAR_STP_TEXTURES:
					# BMW glass materials use their PSH alpha directly with dithered coverage.
					mat.node_tree.links.new(texture_node.outputs['Alpha'], alpha_input)
				else:
					attribute = nodes.get("NFS PS1 face semi-transparency") or nodes.new('ShaderNodeAttribute')
					attribute.name = "NFS PS1 face semi-transparency"
					attribute.attribute_name = "nfs_ps1_semitrans"
					less_than = nodes.get("NFS PS1 fully transparent pixel") or nodes.new('ShaderNodeMath')
					less_than.name = "NFS PS1 fully transparent pixel"
					less_than.operation = 'LESS_THAN'
					less_than.inputs[1].default_value = 0.25
					one_minus_alpha = nodes.get("NFS PS1 inverse texture alpha") or nodes.new('ShaderNodeMath')
					one_minus_alpha.name = "NFS PS1 inverse texture alpha"
					one_minus_alpha.operation = 'SUBTRACT'
					one_minus_alpha.inputs[0].default_value = 1.0
					face_gate = nodes.get("NFS PS1 transparency face gate") or nodes.new('ShaderNodeMath')
					face_gate.name = "NFS PS1 transparency face gate"
					face_gate.operation = 'MAXIMUM'
					combined = nodes.get("NFS PS1 gated transparency") or nodes.new('ShaderNodeMath')
					combined.name = "NFS PS1 gated transparency"
					combined.operation = 'MULTIPLY'
					final_alpha = nodes.get("NFS PS1 final alpha") or nodes.new('ShaderNodeMath')
					final_alpha.name = "NFS PS1 final alpha"
					final_alpha.operation = 'SUBTRACT'
					final_alpha.inputs[0].default_value = 1.0
					mat.node_tree.links.new(texture_node.outputs['Alpha'], less_than.inputs[0])
					mat.node_tree.links.new(texture_node.outputs['Alpha'], one_minus_alpha.inputs[1])
					mat.node_tree.links.new(attribute.outputs['Fac'], face_gate.inputs[0])
					mat.node_tree.links.new(less_than.outputs[0], face_gate.inputs[1])
					mat.node_tree.links.new(face_gate.outputs[0], combined.inputs[0])
					mat.node_tree.links.new(one_minus_alpha.outputs[0], combined.inputs[1])
					mat.node_tree.links.new(combined.outputs[0], final_alpha.inputs[1])
					mat.node_tree.links.new(final_alpha.outputs[0], alpha_input)
			if not any(link.from_node == shader and link.to_node == output for link in mat.node_tree.links):
				mat.node_tree.links.new(shader.outputs['BSDF'], output.inputs['Surface'])
			try:
				if textureIndex in (4, 13):
					mat.surface_render_method = 'DITHERED'
				else:
					mat.surface_render_method = 'BLENDED' if textureIndex in ACTIVE_CAR_SEMITRANS_TEXTURES else 'DITHERED'
			except (AttributeError, TypeError, ValueError):
				pass
		
		if mat.name not in me_ob.materials:
			me_ob.materials.append(mat)
		
		BMFace.material_index = me_ob.materials.find(mat.name)
		
		for loop, uv in zip(BMFace.loops, face_uvs):
			loop[uv_layer].uv = uv
	
	#Finish up, write the bmesh back to the mesh
	bm.to_mesh(me_ob)
	bm.free()
	semitrans_attribute = me_ob.attributes.new(name="nfs_ps1_semitrans", type='FLOAT', domain='FACE')
	for polygon in me_ob.polygons:
		semitrans_attribute.data[polygon.index].value = 1.0 if (faces[polygon.index][0] & 1) else 0.0
	
	if has_some_normal_data:
		temp = []
		for data in normal_data:
			temp.append(data[1])
		normal_data = temp[:]
		
		me_ob.normals_split_custom_set_from_vertices( normal_data )
	else:
		me_ob.update()
	
	return obj


def get_R3DCar_ObjectInfo(index):
	R3DCar_ObjectInfo = {0: [0x00, 0x49, 0x00, 0x01, 0x00, 0x00],
						 1: [0x00, 0x00, 0x00, 0x00, 0x01, 0x00],
						 2: [0x20, 0x02, 0x01, 0x01, 0x00, 0x00],
						 3: [0x30, 0x00, 0x01, 0x01, 0x00, 0x00],
						 4: [0xF8, 0x00, 0x00, 0x00, 0x01, 0x00],
						 5: [0xF0, 0x08, 0x0A, 0x0A, 0x00, 0x00],
						 6: [0xE0, 0x00, 0x0C, 0x00, 0x00, 0x00],
						 7: [0xE0, 0x00, 0x00, 0x0C, 0x00, 0x00],
						 8: [0xEC, 0x89, 0x0B, 0x0B, 0x00, 0x0B],
						 9: [0xF0, 0x88, 0x0B, 0x0B, 0x00, 0x0B],
						 10: [0xEC, 0x89, 0x0C, 0x0C, 0x00, 0x0C],
						 11: [0xF0, 0x88, 0x0C, 0x0C, 0x00, 0x0C],
						 12: [0xE8, 0x00, 0x01, 0x00, 0x00, 0x00],
						 13: [0xE8, 0x00, 0x00, 0x01, 0x00, 0x00],
						 14: [0xD4, 0x00, 0x11, 0x00, 0x00, 0x00],
						 15: [0xD4, 0x00, 0x11, 0x00, 0x00, 0x00],
						 16: [0xE1, 0x08, 0x01, 0x00, 0x00, 0x00],
						 17: [0xE1, 0x08, 0x00, 0x01, 0x00, 0x00],
						 18: [0xD4, 0x00, 0x12, 0x12, 0x12, 0x00],
						 19: [0xE2, 0x00, 0x01, 0x00, 0x00, 0x00],
						 20: [0xE2, 0x00, 0x00, 0x01, 0x00, 0x00],
						 21: [0xD4, 0x00, 0x13, 0x13, 0x13, 0x00],
						 22: [0xE2, 0x18, 0x0F, 0x10, 0x00, 0x00],
						 23: [0xE2, 0x08, 0x00, 0x01, 0x00, 0x00],
						 24: [0xD4, 0x10, 0x14, 0x14, 0x14, 0x00],
						 25: [0xE2, 0x18, 0x0F, 0x10, 0x00, 0x00],
						 26: [0xE2, 0x08, 0x00, 0x01, 0x00, 0x00],
						 27: [0xD4, 0x10, 0x15, 0x15, 0x15, 0x00],
						 28: [0xE8, 0x08, 0x01, 0x00, 0x00, 0x00],
						 29: [0xE8, 0x08, 0x00, 0x01, 0x00, 0x00],
						 30: [0xD4, 0x00, 0x16, 0x16, 0x16, 0x00],
						 31: [0xD8, 0x00, 0x01, 0x01, 0x00, 0x00],
						 32: [0xF4, 0x00, 0x0D, 0x00, 0x00, 0x00],
						 33: [0xF4, 0x00, 0x0E, 0x00, 0x00, 0x00],
						 34: [0xD4, 0x00, 0x11, 0x00, 0x00, 0x00],
						 35: [0x30, 0x00, 0x02, 0x01, 0x00, 0x00],
						 36: [0x28, 0x02, 0x03, 0x00, 0x00, 0x03],
						 37: [0x28, 0x02, 0x03, 0x00, 0x00, 0x03],
						 38: [0x26, 0x02, 0x04, 0x00, 0x00, 0x00],
						 39: [0x24, 0x02, 0x04, 0x00, 0x00, 0x04],
						 40: [0x24, 0x02, 0x04, 0x00, 0x00, 0x04],
						 41: [0x00, 0x49, 0x01, 0x00, 0x00, 0x01],
						 42: [0x00, 0x49, 0x01, 0x00, 0x00, 0x01],
						 43: [0xF0, 0x80, 0x05, 0x00, 0x00, 0x05],
						 44: [0xF0, 0x80, 0x06, 0x00, 0x00, 0x06],
						 45: [0xE8, 0x89, 0x07, 0x07, 0x00, 0x07],
						 46: [0xE8, 0x89, 0x08, 0x08, 0x00, 0x08],
						 47: [0x1F, 0x00, 0x01, 0x01, 0x00, 0x00],
						 48: [0x1F, 0x00, 0x01, 0x01, 0x00, 0x00],
						 49: [0x20, 0x00, 0x01, 0x00, 0x00, 0x00],
						 50: [0x20, 0x00, 0x01, 0x00, 0x00, 0x00],
						 51: [0x20, 0x00, 0x09, 0x01, 0x00, 0x00],
						 52: [0x20, 0x00, 0x09, 0x01, 0x00, 0x00],
						 53: [0x20, 0x00, 0x01, 0x00, 0x00, 0x00],
						 54: [0x20, 0x00, 0x01, 0x00, 0x00, 0x00],
						 55: [0x20, 0x00, 0x09, 0x01, 0x00, 0x00],
						 56: [0x20, 0x00, 0x09, 0x01, 0x00, 0x00]}
	
	return R3DCar_ObjectInfo[index]


def get_geoPartNames(index):
	geoPartNames = {0: "Body Medium",
					1: "Body Low",
					2: "Body Undertray",
					3: "Wheel Wells",
					4: "Wheel Squares (LOW)",
					5: "Wheel Shadow",
					6: "HI Upgrade Front Lip",
					7: "MID Upgrade Front Lip",
					8: "Spoiler Original",
					9: "Spoiler Uprights",
					10: "Spoiler Upgraded",
					11: "Spoiler Upgraded Uprights ",
					12: "HI Fog Lights and Rear bumper",
					13: "MID Fog Lights and Rear bumper",
					14: "Wing Mirror Attachment Points",
					15: "Wheel Attachment Points",
					16: "HI Brake Light Quads",
					17: "MID Brake Light Quads",
					18: "Unknown Rear Light tris",
					19: "Rear Inner Light Quads",
					20: "Rear Inner Light Quads rotated",
					21: "Rear Inner Light Tris",
					22: "HI Front Light quads",
					23: "MID Front Light quads",
					24: "Front Light triangles",
					25: "Rear Main Light Quads",
					26: "Rear Main Light Quads dup",
					27: "Rear Main Light Tris",
					28: "HI Headlight Housing",
					29: "MID Headlight Housing",
					30: "Front Headlight light Pos",
					31: "Logo and Rear Numberplate",
					32: "Exhaust Tips",
					33: "Upgrade Exhaust Tips",
					34: "Mid Body F/R Triangles",
					35: "Interior Cutoff + Driver Pos",
					36: "Cabin",
					37: "Steering Wheel",
					38: "Driver Body",
					39: "Driver Right Arm",
					40: "Driver Left Arm",
					41: "Right Body High",
					42: "Left Body High",
					43: "Right Wing Mirror",
					44: "Left Wing Mirror",
					45: "Front Right Light Bucket",
					46: "Front Left Light bBucket",
					47: "Front Right Wheel",
					48: "Front Left Wheel",
					49: "HI Front Left Tire",
					50: "HI Front Right Tire",
					51: "Front Right Tire",
					52: "Front Left Tire",
					53: "HI Rear Left Tire",
					54: "HI Rear Right Tire",
					55: "Rear Right Wheel",
					56: "Rear Left Wheel"}
	#Thanks to rata536 and Hypercycle for this list
	return geoPartNames[index]


def int_to_id(id):
	id = str(hex(int(id)))[2:].upper().zfill(8)
	id = '_'.join([id[::-1][x : x+2][::-1] for x in range(0, len(id), 2)])
	return id


def clearScene(context): # OK
	#for obj in bpy.context.scene.objects:
	#	obj.select_set(True)
	#bpy.ops.object.delete()

	for block in bpy.data.objects:
		#if block.users == 0:
		bpy.data.objects.remove(block, do_unlink = True)

	for block in bpy.data.meshes:
		if block.users == 0:
			bpy.data.meshes.remove(block)

	for block in bpy.data.materials:
		if block.users == 0:
			bpy.data.materials.remove(block)

	for block in bpy.data.textures:
		if block.users == 0:
			bpy.data.textures.remove(block)

	for block in bpy.data.images:
		if block.users == 0:
			bpy.data.images.remove(block)
	
	for block in bpy.data.cameras:
		if block.users == 0:
			bpy.data.cameras.remove(block)
	
	for block in bpy.data.lights:
		if block.users == 0:
			bpy.data.lights.remove(block)
	
	for block in bpy.data.armatures:
		if block.users == 0:
			bpy.data.armatures.remove(block)
	
	for block in bpy.data.collections:
		if block.users == 0:
			bpy.data.collections.remove(block)
		else:
			bpy.data.collections.remove(block, do_unlink=True)


@orientation_helper(axis_forward='-Y', axis_up='Z')
class ImportNFSHSPS1(Operator, ImportHelper):
	"""Load a Need for Speed High Stakes (1999) PS1 model file"""
	bl_idname = "import_nfshsps1.data"	# important since its how bpy.ops.import_test.some_data is constructed
	bl_label = "Import models"
	bl_options = {'PRESET'}
	
	# ImportHelper mixin class uses this
	filename_ext = ".geo"
	
	filter_glob: StringProperty(
			options={'HIDDEN'},
			default="*.geo",
			maxlen=255,	 # Max internal buffer length, longer would be clamped.
			)
	
	files: CollectionProperty(
			type=OperatorFileListElement,
			)
	
	directory: StringProperty(
			# subtype='DIR_PATH' is not needed to specify the selection mode.
			subtype='DIR_PATH',
			)
	
	# List of operator properties, the attributes will be assigned
	# to the class instance from the operator settings before calling.
	
	is_traffic: BoolProperty(
			name="Is traffic",
			description="Check if the importing vehicle is a traffic",
			default=False,
			)
	
	clear_scene: BoolProperty(
			name="Clear scene",
			description="Check in order to clear the scene",
			default=True,
			)

	mirror_left_right: BoolProperty(
			name="Mirror left/right",
			description="Mirror the vehicle across its centerline to place the driver on the left and correct mirrored side markings",
			default=True,
			)
	
	def execute(self, context): # OK
		global_matrix = axis_conversion(from_forward='Z', from_up='Y', to_forward=self.axis_forward, to_up=self.axis_up).to_4x4()
		
		if len(self.files) > 1:
			os.system('cls')
		
			files_path = []
			for file_elem in self.files:
				files_path.append(os.path.join(self.directory, file_elem.name))
			
			print("Importing %d files" % len(files_path))
			
			if self.clear_scene == True:
				print("Clearing initial scene...")
				clearScene(context)
				print("Setting 'clear_scene' to False now...")
				self.clear_scene = False
			
			print()
			
			for file_path in files_path:
				status = main(context, file_path, self.is_traffic, self.clear_scene, global_matrix, self.mirror_left_right)
				
				if status == {"CANCELLED"}:
					self.report({"ERROR"}, "Importing of file %s has been cancelled. Check the system console for information." % os.path.splitext(os.path.basename(file_path))[0])
				
				print()
				
			return {'FINISHED'}
		elif os.path.isfile(self.filepath) == False:
			os.system('cls')
			
			files_path = []
			for file in os.listdir(self.filepath):
				file_path = os.path.join(self.filepath, file)
				if os.path.isfile(file_path) == True:
					files_path.append(file_path)
				print("Importing %d files" % len(files_path))
			
			for file_path in files_path:
				status = main(context, file_path, self.is_traffic, self.clear_scene, global_matrix, self.mirror_left_right)
				
				if status == {"CANCELLED"}:
					self.report({"ERROR"}, "Importing of file %s has been cancelled. Check the system console for information." % os.path.splitext(os.path.basename(file_path))[0])
				
				print()
				
			return {'FINISHED'}
		else:
			os.system('cls')
			
			status = main(context, self.filepath, self.is_traffic, self.clear_scene, global_matrix, self.mirror_left_right)
			
			if status == {"CANCELLED"}:
				self.report({"ERROR"}, "Importing has been cancelled. Check the system console for information.")
			
			return status
	
	def draw(self, context):
		layout = self.layout
		layout.use_property_split = True
		layout.use_property_decorate = False  # No animation.
		
		sfile = context.space_data
		operator = sfile.active_operator
		
		##
		box = layout.box()
		split = box.split(factor=0.75)
		col = split.column(align=True)
		col.label(text="Settings", icon="SETTINGS")
		
		box.prop(operator, "is_traffic")
		box.prop(operator, "mirror_left_right")
		
		##
		box = layout.box()
		split = box.split(factor=0.75)
		col = split.column(align=True)
		col.label(text="Preferences", icon="OPTIONS")
		
		box.prop(operator, "clear_scene")
		
		##
		box = layout.box()
		split = box.split(factor=0.75)
		col = split.column(align=True)
		col.label(text="Blender orientation", icon="OBJECT_DATA")
		
		row = box.row(align=True)
		row.label(text="Forward axis")
		row.use_property_split = False
		row.prop_enum(operator, "axis_forward", 'X', text='X')
		row.prop_enum(operator, "axis_forward", 'Y', text='Y')
		row.prop_enum(operator, "axis_forward", 'Z', text='Z')
		row.prop_enum(operator, "axis_forward", '-X', text='-X')
		row.prop_enum(operator, "axis_forward", '-Y', text='-Y')
		row.prop_enum(operator, "axis_forward", '-Z', text='-Z')
		
		row = box.row(align=True)
		row.label(text="Up axis")
		row.use_property_split = False
		row.prop_enum(operator, "axis_up", 'X', text='X')
		row.prop_enum(operator, "axis_up", 'Y', text='Y')
		row.prop_enum(operator, "axis_up", 'Z', text='Z')
		row.prop_enum(operator, "axis_up", '-X', text='-X')
		row.prop_enum(operator, "axis_up", '-Y', text='-Y')
		row.prop_enum(operator, "axis_up", '-Z', text='-Z')


def menu_func_import(self, context): # OK
	pcoll = preview_collections["main"]
	my_icon = pcoll["my_icon"]
	self.layout.operator(ImportNFSHSPS1.bl_idname, text="Need for Speed High Stakes (1999) PS1 (.geo)", icon_value=my_icon.icon_id)


classes = (
		ImportNFSHSPS1,
)

preview_collections = {}


def register(): # OK
	import bpy.utils.previews
	pcoll = bpy.utils.previews.new()
	
	my_icons_dir = os.path.join(os.path.dirname(__file__), "polly_icons")
	pcoll.load("my_icon", os.path.join(my_icons_dir, "nfshs_ps1_icon.png"), 'IMAGE')

	preview_collections["main"] = pcoll
	
	for cls in classes:
		bpy.utils.register_class(cls)
	bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister(): # OK
	for pcoll in preview_collections.values():
		bpy.utils.previews.remove(pcoll)
	preview_collections.clear()
	
	for cls in classes:
		bpy.utils.unregister_class(cls)
	bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)


if __name__ == "__main__":
	register()

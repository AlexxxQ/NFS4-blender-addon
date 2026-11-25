"""Import the static road mesh from a Need for Speed High Stakes PS1 track GRP."""

import os
import struct
from array import array

import bpy
from bpy.types import Operator
from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import ImportHelper, axis_conversion, orientation_helper

from . import import_nfshs_ps1_models as geo_import


GROUP_HEADER_SIZE = 16
MAX_GROUPS = 100000
TRACK_ATLAS_WIDTH = 4096
TRACK_PAGE_HEIGHT = 256
TRACK_ATLAS_HEIGHT = TRACK_PAGE_HEIGHT * 3


def _groups(data, offset, limit):
	"""Yield validated serialized groups in a parent group."""
	if offset < 0 or offset + GROUP_HEADER_SIZE > limit:
		return
	group_type, length, _dummy, count = struct.unpack_from('<4i', data, offset)
	if length < GROUP_HEADER_SIZE or offset + length > limit or count < 0 or count > MAX_GROUPS:
		return
	pos = offset + GROUP_HEADER_SIZE
	end = offset + length
	for _ in range(count):
		if pos + GROUP_HEADER_SIZE > end:
			return
		child_type, child_length, _child_dummy, child_count = struct.unpack_from('<4i', data, pos)
		if child_length < GROUP_HEADER_SIZE or pos + child_length > end or child_count < 0 or child_count > MAX_GROUPS:
			return
		yield (child_type, pos, child_length, child_count)
		pos = (pos + child_length + 3) & ~3


def _children(data, parent):
	_, offset, length, _ = parent
	return list(_groups(data, offset, offset + length))


def _first(groups, kind):
	return next((group for group in groups if group[0] == kind), None)


def _payload(data, group):
	_, offset, length, _ = group
	return offset + GROUP_HEADER_SIZE, offset + length


def _decode_geometry(data, chunk_group, chunk_index, center, scale_factor):
	chunk_children = _children(data, chunk_group)
	geom_group = _first(chunk_children, 0x17)
	if geom_group is None:
		return None
	geom_children = _children(data, geom_group)
	vertices_group = _first(geom_children, 0x18)
	quad_group = _first(geom_children, 0x19)
	strip_group = _first(geom_children, 0x1a)
	meta_group = _first(geom_children, 0x1b)
	if vertices_group is None:
		return None

	start, end = _payload(data, vertices_group)
	vertex_bytes = end - start
	if vertex_bytes == 0 or vertex_bytes % 8:
		return None
	vertices = []
	for pos in range(start, end, 8):
		x, y, z, _light = struct.unpack_from('<4h', data, pos)
		# Chunk centers are fixed-point game coordinates; vertices are local PS1 units.
		vertices.append(((center[0] / 1024.0 + x) * scale_factor,
		                 (center[1] / 1024.0 + y) * scale_factor,
		                 (center[2] / 1024.0 + z) * scale_factor))

	faces, material_ids = [], []
	quad_start, quad_end = _payload(data, quad_group) if quad_group else (0, 0)
	quad_total = (quad_end - quad_start) // 6
	counts = [0] * 6
	if meta_group is not None:
		meta_start, meta_end = _payload(data, meta_group)
		if meta_end - meta_start >= 24:
			counts = [data[meta_start + 12 + index * 2] for index in range(6)]

	def append_direct_quads(quad_data, quad_count, vertex_offset=0):
		if quad_data is None or quad_count <= 0:
			return
		qstart, qend = _payload(data, quad_data)
		available = (qend - qstart) // 6
		for q in range(min(quad_count, available)):
			mat, a, b, c, d = struct.unpack_from('<h4B', data, qstart + q * 6)
			face = tuple(vertex_offset + value for value in (b, a, d, c))
			if max(face) < len(vertices):
				faces.append(face)
				material_ids.append(max(0, mat))
	# The game's high-detail draw path uses strips and the final high-detail quad set.
	if strip_group is not None:
		pos, strip_end = _payload(data, strip_group)
		for _ in range(strip_group[3]):
			if pos + 4 > strip_end:
				break
			top, bottom, signed_count, size = struct.unpack_from('<BBbB', data, pos)
			count = signed_count & 0xff
			if size < 4 or pos + size > strip_end or 4 + count * 2 > size:
				break
			for q in range(count):
				mat = struct.unpack_from('<h', data, pos + 4 + q * 2)[0]
				quad = (top + q, top + q + 1, bottom + q + 1, bottom + q)
				if max(quad) < len(vertices):
					faces.append(quad)
					material_ids.append(max(0, mat))
			pos += size

	# In the game, direct high-resolution faces follow all lower-resolution sets.
	high_count = counts[5]
	high_start = counts[0] + counts[1] + counts[4]
	if high_count and high_start + high_count <= quad_total:
		# Match renderQuads[3] in Chunk::InstanceGroup: the high LOD follows
		# quad sets 0, 1, and 4. Other count sets have separate quad buffers.
		pos = quad_start + high_start * 6
		for q in range(high_count):
			mat, a, b, c, d = struct.unpack_from('<h4B', data, pos + q * 6)
			face = (b, a, d, c)
			if max(face) < len(vertices):
				faces.append(face)
				material_ids.append(max(0, mat))

	if not faces:
		low_strip_group = _first(geom_children, 0x25)
		if low_strip_group is not None:
			pos, strip_end = _payload(data, low_strip_group)
			for _ in range(low_strip_group[3]):
				if pos + 4 > strip_end:
					break
				top, bottom, signed_count, size = struct.unpack_from('<BBbB', data, pos)
				count = signed_count & 0xff
				if size < 4 or pos + size > strip_end or 4 + count * 2 > size:
					break
				for q in range(count):
					mat = struct.unpack_from('<h', data, pos + 4 + q * 2)[0]
					face = (top + q, top + q + 1, bottom + q + 1, bottom + q)
					if max(face) < len(vertices):
						faces.append(face)
						material_ids.append(max(0, mat))
				pos += size

	# Chunk-local object polygons use their own vertex and quad groups.
	object_vertices_group = _first(geom_children, 0x27)
	if object_vertices_group is not None:
		obj_start, obj_end = _payload(data, object_vertices_group)
		if (obj_end - obj_start) % 8 == 0:
			vertex_offset = len(vertices)
			for pos in range(obj_start, obj_end, 8):
				x, y, z, _light = struct.unpack_from('<4h', data, pos)
				vertices.append(((center[0] / 1024.0 + x) * scale_factor,
				                 (center[1] / 1024.0 + y) * scale_factor,
				                 (center[2] / 1024.0 + z) * scale_factor))
			append_direct_quads(_first(geom_children, 0x28), counts[2], vertex_offset)
			append_direct_quads(_first(geom_children, 0x29), counts[3], vertex_offset)

	if not faces:
		return None
	return vertices, faces, material_ids


def _read_material_shapes(data, root_groups):
	persistent = _first(root_groups, 0x21)
	if persistent is None:
		return {}
	material_group = _first(_children(data, persistent), 2)
	if material_group is None:
		return {}
	start, end = _payload(data, material_group)
	shapes = {}
	for index, offset in enumerate(range(start, end - 9, 10)):
		shape_index, _flags, uv_flags = struct.unpack_from('<hBB', data, offset)
		shapes[index] = (shape_index, uv_flags)
	return shapes


def _transform_shape_uvs(uvs, flags):
	# Match the UV corner permutations used by Track::ProcessFlipAndUVFlags.
	permutations = {
		0: (1, 0, 3, 2),
		1: (0, 3, 2, 1),
		2: (2, 0, 3, 1),
		3: (3, 2, 0, 1),
		4: (1, 3, 0, 2),
	}
	modes = []
	if flags & 0x10:
		modes.append(0)
	if flags & 0x40:
		modes.append(1)
	if flags & 0x0e:
		modes.append(2 if flags & 2 else (3 if flags & 4 else 4))
	for mode in modes:
		uvs = tuple(uvs[index] for index in permutations[mode])
	return uvs


def _read_track_psh_atlas(psh_path):
	"""Decode the full PS1 track texture page from packed VRAM words."""
	if not psh_path or not os.path.isfile(psh_path):
		return None
	with open(psh_path, 'rb') as stream:
		data = stream.read()
	if len(data) < 16 or data[:4] != b'SHPP':
		return None
	count = struct.unpack_from('<I', data, 8)[0]
	if count > 4096 or 16 + count * 8 > len(data):
		return None
	pixels = array('f', [0.0]) * (TRACK_ATLAS_WIDTH * TRACK_ATLAS_HEIGHT * 4)
	for index in range(count):
		entry = 16 + index * 8
		offset = struct.unpack_from('<I', data, entry + 4)[0]
		if offset + 16 > len(data):
			continue
		depth_code = data[offset] & 3
		if depth_code not in (0, 1, 2):
			continue
		depth = (4, 8, 16)[depth_code]
		width, height = struct.unpack_from('<hh', data, offset + 4)
		position = struct.unpack_from('<I', data, offset + 12)[0]
		x_words, y = position & 0xfff, (position >> 16) & 0xfff
		x_scale = 4 if depth == 4 else (2 if depth == 8 else 1)
		x0 = x_words * x_scale
		if width <= 0 or height <= 0 or width > 1024 or height > 512:
			continue
		if x0 + width > TRACK_ATLAS_WIDTH or y + height > TRACK_PAGE_HEIGHT:
			continue
		row_bytes = ((width * depth + 15) & ~15) // 8
		pixel_offset = offset + 16
		if pixel_offset + row_bytes * height > len(data):
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
				word = struct.unpack_from('<H', data, palette_offset + color_index * 2)[0]
				alpha = 0.0 if word == 0 else (0.5 if word & 0x8000 else 1.0)
				palette.append(((word & 31) / 31.0, ((word >> 5) & 31) / 31.0,
				                ((word >> 10) & 31) / 31.0, alpha))
		for py in range(height):
			src_row = pixel_offset + py * row_bytes
			layer_y = depth_code * TRACK_PAGE_HEIGHT + y
			dst_row = (TRACK_ATLAS_HEIGHT - 1 - (layer_y + py)) * TRACK_ATLAS_WIDTH
			for px in range(width):
				if depth == 4:
					packed = data[src_row + px // 2]
					color = palette[(packed & 15) if not (px & 1) else (packed >> 4)]
				elif depth == 8:
					color = palette[data[src_row + px]]
				else:
					word = struct.unpack_from('<H', data, src_row + px * 2)[0]
					alpha = 0.0 if word == 0 else (0.5 if word & 0x8000 else 1.0)
					color = ((word & 31) / 31.0, ((word >> 5) & 31) / 31.0,
					         ((word >> 10) & 31) / 31.0, alpha)
				dst = (dst_row + x0 + px) * 4
				pixels[dst] = color[0]
				pixels[dst + 1] = color[1]
				pixels[dst + 2] = color[2]
				pixels[dst + 3] = color[3]
	image = bpy.data.images.new(os.path.basename(psh_path), TRACK_ATLAS_WIDTH, TRACK_ATLAS_HEIGHT, alpha=True)
	image.pixels.foreach_set(pixels)
	image.update()
	image.pack()
	return image


def _read_shape_rects(psh_path):
	if not psh_path or not os.path.isfile(psh_path):
		return {}
	with open(psh_path, 'rb') as stream:
		data = stream.read()
	if len(data) < 16 or data[:4] != b'SHPP':
		return {}
	count = struct.unpack_from('<I', data, 8)[0]
	if count > 4096 or 16 + count * 8 > len(data):
		return {}
	shapes = {}
	for index in range(count):
		entry = 16 + index * 8
		offset = struct.unpack_from('<I', data, entry + 4)[0]
		if offset + 16 > len(data):
			continue
		width, height = struct.unpack_from('<hh', data, offset + 4)
		position = struct.unpack_from('<I', data, offset + 12)[0]
		depth_code = data[offset] & 3
		if depth_code not in (0, 1, 2) or width <= 0 or height <= 0:
			continue
		depth = (4, 8, 16)[depth_code]
		x_words = position & 0xfff
		y = (position >> 16) & 0xfff
		x = x_words * (4 if depth == 4 else 2 if depth == 8 else 1)
		layer_y = depth_code * TRACK_PAGE_HEIGHT + y
		if x + width <= TRACK_ATLAS_WIDTH and y + height <= TRACK_PAGE_HEIGHT:
			shapes[index] = (x, layer_y, width, height)
	return shapes


def _make_track_material(name, image=None):
	material = bpy.data.materials.new(name)
	material.use_nodes = True
	if image is None:
		return material
	material["nfs_ps1_shape_atlas"] = image.name
	if hasattr(material, 'surface_render_method'):
		material.surface_render_method = 'DITHERED'
	shader = material.node_tree.nodes.get('Principled BSDF')
	texture = material.node_tree.nodes.new('ShaderNodeTexImage')
	texture.label = 'NFS PS1 track PSH atlas'
	texture.image = image
	texture.interpolation = 'Closest'
	if shader:
		material.node_tree.links.new(texture.outputs['Color'], shader.inputs['Base Color'])
		material.node_tree.links.new(texture.outputs['Alpha'], shader.inputs['Alpha'])
	return material


def import_track(context, filepath, clear_scene=True, scale_factor=0.01, global_matrix=None):
	with open(filepath, 'rb') as stream:
		data = stream.read()
	if len(data) < GROUP_HEADER_SIZE:
		raise ValueError('Файл слишком мал для группы данных PS1.')
	root_type, root_length, _root_dummy, _root_count = struct.unpack_from('<4i', data, 0)
	if root_type != 0x1e or root_length > len(data):
		raise ValueError('Неизвестный заголовок трассы. Выберите основной файл трассы .GRP.')
	root = (root_type, 0, root_length, _root_count)
	root_children = _children(data, root)
	if not root_children:
		raise ValueError('Не удалось прочитать список групп. Выберите основной файл трассы .GRP.')
	centers_group = _first(root_children, 0x20)
	chunk_groups = [g for g in root_children if g[0] == 0x1d]
	if not chunk_groups:
		raise ValueError('В файле нет чанков геометрии трассы (группы 0x1D).')
	centers = []
	if centers_group is not None:
		start, end = _payload(data, centers_group)
		for pos in range(start, end - 11, 12):
			centers.append(struct.unpack_from('<3i', data, pos))

	track_name = os.path.splitext(os.path.basename(filepath))[0]
	psh_path = os.path.join(os.path.dirname(filepath), track_name + '0.PSH')
	track_image = None
	track_image = _read_track_psh_atlas(psh_path)
	shape_rects = _read_shape_rects(psh_path)
	material_shapes = _read_material_shapes(data, root_children)
	collection = bpy.data.collections.new('NFS HS PS1 Track ' + track_name)
	if clear_scene:
		for scene_collection in list(context.scene.collection.children):
			context.scene.collection.children.unlink(scene_collection)
	context.scene.collection.children.link(collection)
	objects_created = 0
	for chunk_index, chunk_group in enumerate(chunk_groups):
		center = centers[chunk_index] if chunk_index < len(centers) else (0, 0, 0)
		decoded = _decode_geometry(data, chunk_group, chunk_index, center, scale_factor)
		if decoded is None:
			continue
		vertices, faces, material_ids = decoded
		mesh = bpy.data.meshes.new('TrackChunk_%03d_Mesh' % chunk_index)
		mesh.from_pydata(vertices, [], faces)
		mesh.update()
		obj = bpy.data.objects.new('TrackChunk_%03d' % chunk_index, mesh)
		collection.objects.link(obj)
		if global_matrix is not None:
			obj.matrix_world = global_matrix
		used_material_ids = sorted(set(material_ids))
		material_slots = {}
		for mat_id in used_material_ids:
			material_slots[mat_id] = len(mesh.materials)
			shape_index = material_shapes.get(mat_id, (-1, 0))[0]
			mesh.materials.append(_make_track_material('Track material %02d (shape %d)' % (mat_id, shape_index), track_image))
		for face_index, mat_id in enumerate(material_ids):
			mesh.polygons[face_index].material_index = material_slots[mat_id]
		if track_image:
			uv_layer = mesh.uv_layers.new(name='NFS PS1 atlas UV')
			for face_index, mat_id in enumerate(material_ids):
				shape_index, uv_flags = material_shapes.get(mat_id, (-1, 0))
				rect = shape_rects.get(shape_index)
				if rect:
					x, y, width, height = rect
					u0, u1 = x / TRACK_ATLAS_WIDTH, (x + width) / TRACK_ATLAS_WIDTH
					v0, v1 = 1.0 - (y + height) / TRACK_ATLAS_HEIGHT, 1.0 - y / TRACK_ATLAS_HEIGHT
					pmx_uvs = ((u0, v1), (u1, v1), (u0, v0), (u1, v0))
				else:
					pmx_uvs = ((0.0, 1.0), (1.0, 1.0), (0.0, 0.0), (1.0, 0.0))
				pmx_uvs = _transform_shape_uvs(pmx_uvs, uv_flags)
				# Face loops follow the game's point order [0, 1, 3, 2].
				uvs = (pmx_uvs[0], pmx_uvs[1], pmx_uvs[3], pmx_uvs[2])
				polygon = mesh.polygons[face_index]
				for loop_index, uv in zip(polygon.loop_indices, uvs):
					uv_layer.data[loop_index].uv = uv
		objects_created += 1
	if objects_created == 0:
		bpy.data.collections.remove(collection)
		raise ValueError('Чанки найдены, но дорожные полигоны не распознаны. Формат этой трассы требует дополнительного разбора.')
	return collection, objects_created


@orientation_helper(axis_forward='-Y', axis_up='Z')
class IMPORT_OT_nfshs_ps1_track(Operator, ImportHelper):
	bl_idname = 'import_scene.nfshs_ps1_track'
	bl_label = 'Import NFS HS PS1 Track Geometry'
	bl_options = {'REGISTER', 'UNDO'}

	filename_ext = '.grp'
	filter_glob: StringProperty(default='*.grp', options={'HIDDEN'})
	clear_scene: BoolProperty(name='Clear Scene', default=True)
	scale_factor: FloatProperty(name='Scale', description='Scales the PS1 track world coordinates', default=0.01, min=0.0001, max=1.0)

	def execute(self, context):
		try:
			global_matrix = axis_conversion(
				from_forward='Z',
				from_up='Y',
				to_forward=self.axis_forward,
				to_up=self.axis_up,
			).to_4x4()
			collection, count = import_track(
				context,
				self.filepath,
				self.clear_scene,
				self.scale_factor,
				global_matrix,
			)
		except Exception as error:
			self.report({'ERROR'}, str(error))
			return {'CANCELLED'}
		self.report({'INFO'}, 'Imported %d track chunks into %s' % (count, collection.name))
		return {'FINISHED'}


def menu_import(self, context):
	self.layout.operator(IMPORT_OT_nfshs_ps1_track.bl_idname, text='NFS High Stakes PS1 Track (.grp)')


def register():
	bpy.utils.register_class(IMPORT_OT_nfshs_ps1_track)
	bpy.types.TOPBAR_MT_file_import.append(menu_import)


def unregister():
	bpy.types.TOPBAR_MT_file_import.remove(menu_import)
	bpy.utils.unregister_class(IMPORT_OT_nfshs_ps1_track)


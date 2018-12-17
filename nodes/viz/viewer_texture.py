# This file is part of project Sverchok. It's copyrighted by the contributors
# recorded in the version control history of the file, available from
# its original location https://github.com/nortikin/sverchok/commit/master
#  
# SPDX-License-Identifier: GPL3
# License-Filename: LICENSE

# contribubtions:
#   kalwalt
#   zeffii

import os
import numpy as np


import bpy
import gpu
import bgl
from gpu_extras.batch import batch_for_shader
from bpy.props import (
    FloatProperty, EnumProperty, StringProperty, BoolProperty, IntProperty
)

from sverchok.utils.context_managers import sv_preferences
from sverchok.data_structure import updateNode, node_id
from sverchok.node_tree import SverchCustomTreeNode
from sverchok.ui import bgl_callback_nodeview as nvBGL2
from sverchok.ui import sv_image as svIMG

from sverchok.utils.sv_operator_mixins import (
    SvGenericDirectorySelector, SvGenericCallbackWithParams
)


class SvTextureViewerOperator(bpy.types.Operator, SvGenericCallbackWithParams):
    """ Save the image with passed settings """
    bl_idname = "node.sv_texview_callback"
    bl_label = "Execute a function on the calling node"


class SvTextureViewerDirSelect(bpy.types.Operator, SvGenericDirectorySelector):
    """ Pick the directory to store images in """
    bl_idname = "node.sv_texview_dirselect"
    bl_label = "Pick directory"



size_tex_list = [
    ('XS', 'XS', 'extra small squared tex: 64px', '', 64),
    ('S', 'S', 'small squared tex: 128px', '', 128),
    ('M', 'M', 'medium squared tex: 256px', '', 256),
    ('L', 'L', 'large squared tex: 512px', '', 512),
    ('XL', 'XL', 'extra large squared tex: 1024px', '', 1024),
    ('USER', 'USER', 'extra large squared tex: 1024px', '', 0)
]

size_tex_dict = {item[0]: item[4] for item in size_tex_list}

bitmap_format_list = [
    ('PNG', '.png', 'save texture in .png format', '', 0),
    ('TARGA', '.tga', 'save texture in .tga format', '', 1),
    ('TARGA_RAW', '.tga (raw)', 'save texture in .tga(raw) format', '', 2),
    ('TIFF', '.tiff', 'save texture in .tiff format', '', 3),
    ('BMP', '.bmp', 'save texture in .tiff format', '', 4),
    ('JPEG', '.jpeg', 'save texture in .jpeg format', '', 5),
    ('JPEG2000', '.jp2', 'save texture in .jpeg (2000) format', '', 6),
    ('OPEN_EXR_MULTILAYER', '.exr (multilayer)', 'save texture in .exr (multilayer) format', '', 7),
    ('OPEN_EXR', '.exr', 'save texture in .exr format', '', 8),
]

format_mapping = {
    'TARGA': 'tga',
    'TARGA_RAW': 'tga',
    'JPEG2000': 'jp2',
    'OPEN_EXR_MULTILAYER': 'exr',
    'OPEN_EXR': 'exr',
}

gl_color_list = [
    ('BW', 'bw', 'grayscale texture', '', 0),
    ('RGB', 'rgb', 'rgb colored texture', '', 1),
    ('RGBA', 'rgba', 'rgba colored texture', '', 2)
]

gl_color_dict = {
    'BW': 6403,  # GL_RED
    'RGB': 6407,  # GL_RGB
    'RGBA': 6408  # GL_RGBA
}

factor_buffer_dict = {
    'BW': 1,  # GL_RED
    'RGB': 3,  # GL_RGB
    'RGBA': 4  # GL_RGBA
}

vertex_shader = '''
    uniform mat4 ModelViewProjectionMatrix;
    
    /* Keep in sync with intern/opencolorio/gpu_shader_display_transform_vertex.glsl */
    
    in vec2 texCoord;
    in vec2 pos;

    out vec2 texCoord_interp;

    void main()
    {
       gl_Position = ModelViewProjectionMatrix * vec4(pos.xy, 0.0f, 1.0f);
       gl_Position.z = 1.0;
       texCoord_interp = texCoord;
    }
'''

fragment_shader = '''
    in vec2 texCoord_interp;
    out vec4 fragColor;

    uniform sampler2D image;
    uniform bool ColorMode;

    void main()
    {
        if (ColorMode) {
           fragColor = texture(image, texCoord_interp);
        }else{
           fragColor = texture(image, texCoord_interp).rrrr;
        }
    }
'''
cMode = ((False,))

def transfer_to_image(pixels, name, width, height, mode):
    # transfer pixels(data) from Node tree to image viewer
    image = bpy.data.images.get(name)
    if not image:
        image = bpy.data.images.new(name, width, height, alpha=False)
        image.pack
    else:
        image.scale(width, height)
    svIMG.pass_buffer_to_image(mode, image, pixels, width, height)
    image.update_tag()


def init_texture(width, height, texname, texture, clr):
    # function to init the texture
    bgl.glPixelStorei(bgl.GL_UNPACK_ALIGNMENT, 1)

    bgl.glEnable(bgl.GL_TEXTURE_2D)
    bgl.glBindTexture(bgl.GL_TEXTURE_2D, texname)
    bgl.glActiveTexture(bgl.GL_TEXTURE0)

    bgl.glTexParameterf(bgl.GL_TEXTURE_2D, bgl.GL_TEXTURE_WRAP_S, bgl.GL_CLAMP_TO_EDGE)
    bgl.glTexParameterf(bgl.GL_TEXTURE_2D, bgl.GL_TEXTURE_WRAP_T, bgl.GL_CLAMP_TO_EDGE)
    bgl.glTexParameterf(bgl.GL_TEXTURE_2D, bgl.GL_TEXTURE_MAG_FILTER, bgl.GL_LINEAR)
    bgl.glTexParameterf(bgl.GL_TEXTURE_2D, bgl.GL_TEXTURE_MIN_FILTER, bgl.GL_LINEAR)

    bgl.glTexImage2D(
        bgl.GL_TEXTURE_2D,
        0, clr, width, height,
        0, clr, bgl.GL_FLOAT, texture
    )


def simple_screen(x, y, args):
    # draw a simple scren display for the texture
    # border_color = (0.390805, 0.754022, 1.000000, 1.00)
    texture, texname, width, height, batch, shader, cMod = args

    def draw_texture(x=0, y=0, w=30, h=10, texname=texname, c=cMod):
        # function to draw a texture
        bgl.glDisable(bgl.GL_DEPTH_TEST)

        act_tex = bgl.Buffer(bgl.GL_INT, 1)
        bgl.glBindTexture(bgl.GL_TEXTURE_2D, texname)

        shader.bind()
        shader.uniform_int("image", act_tex)
        shader.uniform_bool("ColorMode", c)
        batch.draw(shader)

        # restoring settings
        bgl.glBindTexture(bgl.GL_TEXTURE_2D, act_tex[0])
        bgl.glDisable(bgl.GL_TEXTURE_2D)

    draw_texture(x=x, y=y, w=width, h=height, texname=texname, c=cMod)


class SvTextureViewerNode(bpy.types.Node, SverchCustomTreeNode):
    """
    Triggers: Texture Viewer node 
    Tooltip: Generate textures and images from inside Sverchok
    """

    bl_idname = 'SvTextureViewerNode'
    bl_label = 'Texture viewer'
    texture = {}

    def wrapped_update(self, context):
        if self.selected_mode == 'USER':
            if len(self.inputs) == 1:
                self.inputs.new('StringsSocket', "Width").prop_name = 'width_custom_tex'
                self.inputs.new('StringsSocket', "Height").prop_name = 'height_custom_tex'
        else:
            if len(self.inputs) == 3:
                self.inputs.remove(self.inputs[-1])
                self.inputs.remove(self.inputs[-1])

        updateNode(self, context)

    def wrapped_updateNode_(self, context):
        self.activate = False

    n_id: StringProperty(default='')
    to_image_viewer: BoolProperty(
        name='Pass', description='Transfer pixels to image viewer',
        default=False, update=wrapped_updateNode_)

    activate: BoolProperty(
        name='Show', description='Activate texture drawing',
        default=True, update=updateNode)

    selected_mode: EnumProperty(
        items=size_tex_list, description="Offers display sizing",
        default="S", update=wrapped_update)

    width_custom_tex: IntProperty(
        min=1, max=1024, default=206, name='Width Tex',
        description="set the custom texture size", update=updateNode)

    height_custom_tex: IntProperty(
        min=1, max=1024, default=124, name='Height Tex',
        description="set the custom texture size", update=updateNode)

    bitmap_format: EnumProperty(
        items=bitmap_format_list,
        description="Offers bitmap saving", default="PNG")

    color_mode: EnumProperty(
        items=gl_color_list, description="Offers color options",
        default="BW", update=updateNode)

    color_mode_save: EnumProperty(
        items=gl_color_list, description="Offers color options",
        default="BW", update=updateNode)

    compression_level: IntProperty(
        min=0, max=100, default=0, name='compression',
        description="set compression level", update=updateNode)

    quality_level: IntProperty(
        min=0, max=100, default=0, name='quality',
        description="set quality level", update=updateNode)

    in_float: FloatProperty(
        min=0.0, max=1.0, default=0.0, name='Float Input',
        description='Input for texture', update=updateNode)

    base_dir: StringProperty(default='/tmp/')
    image_name: StringProperty(default='image_name', description='name (minus filetype)')
    texture_name: StringProperty(
        default='texture',
        description='set name (minus filetype) for exporting to image viewer')
    total_size: IntProperty(default=0)

    @property
    def xy_offset(self):
        a = self.location[:]
        b = int(self.width) + 20
        return int(a[0] + b), int(a[1])

    @property
    def custom_size(self):
        sockets = self.inputs["Width"], self.inputs["Height"]
        return [s.sv_get(deepcopy=False)[0][0] for s in sockets]

    @property
    def texture_width_height(self):
        #  get the width and height for the texture
        if self.selected_mode == 'USER':
            width, height = self.custom_size
        else:
            size_tex = size_tex_dict.get(self.selected_mode)
            width, height = size_tex, size_tex
        return width, height

    def calculate_total_size(self):
        ''' buffer need adequate size multiplying '''
        width, height = self.texture_width_height
        return width * height * factor_buffer_dict.get(self.color_mode)

    def get_buffer(self):
        data = self.inputs['Float'].sv_get(deepcopy=False)
        self.total_size = self.calculate_total_size()

        #  self.make_data_correct_length(data)
        texture = bgl.Buffer(bgl.GL_FLOAT, self.total_size, np.resize(data, self.total_size).tolist())
        return texture

    def draw_buttons(self, context, layout):
        c = layout.column()
        c.label(text="Set texture display:")
        row = c.row()
        row.prop(self, "selected_mode", expand=True)

        nrow = c.row()
        nrow.prop(self, 'activate')
        nrow.prop(self, 'to_image_viewer')
        row = layout.row(align=True)
        row.prop(self, 'color_mode', expand=True)


    def draw_buttons_ext(self, context, layout):
        img_format = self.bitmap_format
        callback_to_self = "node.sv_texview_callback"
        directory_select = "node.sv_texview_dirselect"

        layout.label(text="Save texture as a bitmap")

        layout.separator()
        layout.prop(self, "bitmap_format", text='format')
        layout.separator()

        if img_format == 'PNG':
            row = layout.row()
            row.prop(self, 'compression_level', text='set compression')
            layout.separator()
        if img_format in {'JPEG', 'JPEG2000'}:
            row = layout.row()
            row.prop(self, 'quality_level', text='set quality')
            layout.separator()

        row = layout.row(align=True)
        leftside = row.split(factor=0.7)
        leftside.prop(self, 'image_name', text='')
        rightside = leftside.split().row(align=True)
        rightside.operator(callback_to_self, text="Save").fn_name = "save_bitmap"
        rightside.operator(directory_select, text="", icon='FILE_IMAGE').fn_name = "set_dir"
        transfer = layout.column(align=True)
        transfer.separator()
        transfer.label(text="Transfer to image viewer")
        transfer.prop(self, 'texture_name', text='', icon='EXPORT')

    def draw_label(self):
        if self.selected_mode == 'USER':
            width, height = self.texture_width_height
            label = (self.label or self.name) + ' {0} x {1}'.format(width, height)
        else:
            label = (self.label or self.name) + ' ' + str(size_tex_dict.get(self.selected_mode)) + "^2"
        return label

    def sv_init(self, context):
        self.width = 180
        self.inputs.new('StringsSocket', "Float").prop_name = 'in_float'

    def delete_texture(self):
        n_id = node_id(self)
        if n_id in self.texture:
            names = bgl.Buffer(bgl.GL_INT, 1, [self.texture[n_id]])
            bgl.glDeleteTextures(1, names)

    def process(self):
        if not self.inputs['Float'].is_linked:
            return

        n_id = node_id(self)
        self.delete_texture()
        nvBGL2.callback_disable(n_id)

        size_tex = 0
        width = 0
        height = 0
        if self.color_mode in ('RGB', 'RGBA'):
           cMode = ((True,))
        else:
           cMode = ((False,))

        print(cMode)

        if self.to_image_viewer:

            mode = self.color_mode
            pixels = np.array(self.inputs['Float'].sv_get(deepcopy=False)).flatten()
            width, height = self.texture_width_height
            resized_np_array = np.resize(pixels, self.calculate_total_size())
            transfer_to_image(resized_np_array, self.texture_name, width, height, mode)


        if self.activate:
            texture = self.get_buffer()
            width, height = self.texture_width_height
            x, y = self.xy_offset
            gl_color_constant = gl_color_dict.get(self.color_mode)
    
            name = bgl.Buffer(bgl.GL_INT, 1)
            bgl.glGenTextures(1, name)
            self.texture[n_id] = name[0]
            init_texture(width, height, name[0], texture, gl_color_constant)

            multiplier, scale = self.get_preferences()
            x, y = [x * multiplier, y * multiplier]
            width, height = [width * scale, height * scale]

            batch, shader = self.generate_batch_shader((x, y, width, height, cMode))
            draw_data = {
                'tree_name': self.id_data.name[:],
                'mode': 'custom_function',
                'custom_function': simple_screen,
                'loc': (x, y),
                'args': (texture, self.texture[n_id], width, height, batch, shader, cMode)
            }

            nvBGL2.callback_enable(n_id, draw_data)

    def generate_batch_shader(self, args):
        x, y, w, h, cM = args
        shader = gpu.types.GPUShader(vertex_shader, fragment_shader)
        batch = batch_for_shader(
            shader, 'TRI_FAN',
            {
                "pos": ((x, y), (x + w, y), (x + w, y - h), (x, y - h)),
                "texCoord": ((0, 1), (1, 1), (1, 0), (0, 0)),
            },
        )
        return batch, shader

    def get_preferences(self):
        # adjust render location based on preference multiplier setting
        try:
            with sv_preferences() as prefs:
                multiplier = prefs.render_location_xy_multiplier
                scale = prefs.render_scale
        except:
            # print('did not find preferences - you need to save user preferences')
            multiplier = 1.0
            scale = 1.0
        return multiplier, scale

    def free(self):
        nvBGL2.callback_disable(node_id(self))
        self.delete_texture()

    def copy(self, node):
        # reset n_id on copy
        self.n_id = ''

    def set_dir(self, operator):
        self.base_dir = operator.directory
        print('new base dir:', self.base_dir)
        return {'FINISHED'}

    def push_image_settings(self, scene):
        img_format = self.bitmap_format
        print('img_format is: {0}'.format(img_format))

        img_settings = scene.render.image_settings
        img_settings.quality = self.quality_level
        img_settings.color_depth = '16' if img_format in {'JPEG', 'JPEG2000'} else '8'
        img_settings.compression = self.compression_level
        img_settings.color_mode = self.color_mode
        img_settings.file_format = img_format
        print('settings done!')

    def save_bitmap(self, operator):
        scene = bpy.context.scene
        image_name = self.image_name or 'image_name'
        img_format = self.bitmap_format

        extension = svIMG.get_extension(img_format, format_mapping)
        width, height = self.texture_width_height
        img = svIMG.get_image_by_name(image_name, extension, width, height)
        buf = self.get_buffer()
        svIMG.pass_buffer_to_image(self.color_mode, img, buf, width, height)

        self.push_image_settings(scene)
        desired_path = os.path.join(self.base_dir, self.image_name + extension)
        img.save_render(desired_path, scene)
        print('Bitmap saved!  path is:', desired_path)



classes = [SvTextureViewerOperator, SvTextureViewerDirSelect, SvTextureViewerNode]
register, unregister = bpy.utils.register_classes_factory(classes)
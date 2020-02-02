# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####
from colorsys import rgb_to_hls
from itertools import repeat
import bpy
from bpy.props import EnumProperty, FloatProperty, FloatVectorProperty, StringProperty, BoolProperty
from mathutils import Vector, Matrix, Color

from sverchok.node_tree import SverchCustomTreeNode, throttled
from sverchok.core.socket_data import SvGetSocketInfo
from sverchok.data_structure import updateNode, list_match_func, numpy_list_match_modes, iter_list_match_func
from sverchok.utils.sv_itertools import recurse_f_level_control
from sverchok.utils.modules.color_utils import color_channels

class EmptyTexture():
    def evaluate(self, vec):
        return [1, 1, 1, 1]

def texture_evaluate(params, extract_func):
    vertex, texture = params
    v_vertex = Vector(vertex)
    col = texture.evaluate(v_vertex)
    eval_s = extract_func(col)
    return eval_s


def meshes_texture_evaluate(params, constant, matching_f):
    '''
    This function prepares the data to pass to the evaluate function.

    params are verts, texture,
    - verts should be list as [[[float, float, float],],] (Level 3)
    - texture can be [texture, texture] or [[texture, texture],[texture]] for per vertex texture

    desired_levels = [3, 2 or 3]
    constant are the function options (data that does not need to be matched)
    matching_f stands for list matching formula to use
    '''
    result = []
    color_channel, match_mode = constant
    params = matching_f(params)
    local_match = iter_list_match_func[match_mode]
    extract_func = color_channels[color_channel][1]
    for props in zip(*params):
        verts, texture = props
        if  not type(texture) == list:
            texture = [texture]
        m_texture = local_match([texture])[0]
        result.append([texture_evaluate(v_prop, extract_func) for v_prop in zip(verts, m_texture)])

    return result

color_channels_modes = [(t, t, t, '', color_channels[t][0]) for t in color_channels if not t == 'RGBA']


class SvTextureEvaluateNode(bpy.types.Node, SverchCustomTreeNode):
    """
    Triggers: Scence Texture In
    Tooltip: Evaluate Scene texture at input coordinates

    """

    bl_idname = 'SvTextureEvaluateNode'
    bl_label = 'Texture Evaluate'
    bl_icon = 'FORCE_TEXTURE'


    out_modes = [
        ('NORMAL', 'Single Channel', 'Texture displacement along Vertex Normal', '', 1),
        ('RGB to XYZ', 'RGB', 'Texture displacement with RGB as vector', '', 2),
        ('HSV to XYZ', 'HSV', 'Texture displacement with HSV as vector', '', 3),
        ('HLS to XYZ', 'HLS', 'Texture displacement with HSV as vector', '', 4)]

    texture_coord_modes = [
        ('UV', 'UV coords', 'Input UV coordinates to evaluate texture', '', 1),
        ('Mesh Matrix', 'Mesh Matrix', 'Matrix to apply to verts before evaluating texture', '', 2),
        ('Texture Matrix', 'Texture Matrix', 'Matrix of texture (External Object matrix)', '', 3),

    ]

    @throttled
    def change_mode(self, context):
        outputs = self.outputs
        if self.color_channel not in ['Color', 'RGBA']:
            outputs[0].replace_socket('SvStringsSocket', 'Value')
        else:
            outputs[0].replace_socket('SvColorSocket', 'Color')

    name_texture: StringProperty(
        name='image_name',
        description='image name',
        default='',
        update=updateNode)

    color_channel: EnumProperty(
        name='Component',
        items=color_channels_modes,
        default='Alpha',
        description="Channel to use from texture",
        update=change_mode)

    use_alpha: BoolProperty(default=False, update=updateNode)

    list_match: EnumProperty(
        name="List Match",
        description="Behavior on different list lengths",
        items=numpy_list_match_modes, default="REPEAT",
        update=updateNode)

    def sv_init(self, context):
        self.width = 200
        self.inputs.new('SvVerticesSocket', 'Vertices')
        self.inputs.new('SvStringsSocket', 'Texture').custom_draw = 'draw_texture_socket'

        self.outputs.new('SvStringsSocket', 'Value')


    def draw_texture_socket(self, socket, context, layout):
        if not socket.is_linked:
            c = layout.split(factor=0.3, align=False)
            c.label(text=socket.name+ ':')

            c.prop_search(self, "name_texture", bpy.data, 'textures', text="")
        else:
            layout.label(text=socket.name+ '. ' + SvGetSocketInfo(socket))
    def draw_buttons(self, context, layout):
        c = layout.split(factor=0.3, align=True)
        c.label(text='Channel:')
        c.prop(self, 'color_channel', text="")
        if self.color_channel == 'Color':
            layout.prop(self, 'use_alpha', text="Use Alpha")

    def draw_buttons_ext(self, context, layout):
        '''draw buttons on the N-panel'''
        self.draw_buttons(context, layout)
        layout.prop(self, 'list_match', expand=False)

    def rclick_menu(self, context, layout):
        layout.prop_menu_enum(self, "list_match", text="List Match")

    def process(self):
        inputs, outputs = self.inputs, self.outputs

        if not outputs[0].is_linked:
            return

        result = []

        params = []
        params.append(inputs[0].sv_get(default=[[]], deepcopy=False))
        if not inputs[1].is_linked:
            if not self.name_texture:
                params.append([[EmptyTexture()]])
            else:
                params.append([[bpy.data.textures[self.name_texture]]])
        else:
            params.append(inputs[1].sv_get(default=[[]], deepcopy=False))


        matching_f = list_match_func[self.list_match]
        desired_levels = [3, 2]
        if self.color_channel == 'Color' and self.use_alpha:
            ops = ['RGBA', self.list_match]
        else:
            ops = [self.color_channel, self.list_match]
        result = recurse_f_level_control(params, ops, meshes_texture_evaluate, matching_f, desired_levels)

        self.outputs[0].sv_set(result)



    def draw_label(self):
        if self.hide:
            if not self.inputs['Texture'].is_linked:
                texture = ' ' + self.name_texture
            else:
                texture = ' + texture(s)'
            return 'Displace' + texture +' ' + self.color_channel.title() + ' channel'
        else:
            return self.label or self.name

classes = [SvTextureEvaluateNode]
register, unregister = bpy.utils.register_classes_factory(classes)
#  Copyright 2020-2021 Dragorn421, Sauraen
#
#  This objex2 addon is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This objex2 addon is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this objex2 addon.  If not, see <https://www.gnu.org/licenses/>.

from . import blender_version_compatibility

import bpy
import mathutils
import math

from . import util
from .logging_util import getLogger


def write_skeleton(file_write_skel, global_matrix, object_transform, armature, armature_name_q, bones_ordered):
    log = getLogger('anim')
    fw = file_write_skel
    objex_data = armature.data.objex_bonus
    fw('newskel %s' % armature_name_q)
    if objex_data.type != 'NONE':
        fw(' %s' % objex_data.type)
    fw('\n')
    if objex_data.segment:
        fw('segment %s%s%s\n' % (
            '' if objex_data.segment.startswith('0x') else '0x',
            objex_data.segment,
            ' local' if objex_data.segment_local else ''
        ))
    if objex_data.pbody:
        fw('pbody')
        if objex_data.pbody_parent_object:
            if blender_version_compatibility.no_ID_PointerProperty:
                pbody_parent_object_name = objex_data.pbody_parent_object
            else:
                pbody_parent_object_name = objex_data.pbody_parent_object.name
            fw(' parent %s %s' % (util.quote(pbody_parent_object_name), util.quote(objex_data.pbody_parent_bone)))
        fw('\n')
    indent = 0
    stack = [None]
    transform = blender_version_compatibility.matmul(global_matrix, object_transform)
    for bone in bones_ordered:
        #print('indent=%d bone=%s parent=%s stack=%r' % (indent, bone.name, bone.parent.name if bone.parent else 'None', stack))
        while bone.parent != stack[-1]:
            indent -= 1
            fw('%s-\n' % (' ' * indent))
            stack.pop()
        pos = blender_version_compatibility.matmul(transform, bone.head_local)
        if bone.parent:
            pos = pos.copy()
            pos -= blender_version_compatibility.matmul(transform, bone.parent.head_local)
        else:
            if bone.head_local != mathutils.Vector((0,0,0)):
                log.debug('root bone {} at {!r} does not start at armature origin', bone.name, pos)
        fw('%s+ %s %.6f %.6f %.6f\n' % (' ' * indent, util.quote(bone.name), pos.x, pos.y, pos.z))
        indent += 1
        stack.append(bone)
    while indent > 0:
        indent -= 1
        fw('%s-\n' % (' ' * indent))
    fw('\n')

def order_bones(armature):
    log = getLogger('anim')
    """
    find root bone in armature
    list bones in hierarchy order, preserving the armature order
    """
    bones = armature.data.bones
    root_bone = None
    bones_ordered = []
    skipped_bones = []
    for bone in bones:
        # 421todo skip bones assigned to no vertex if they're root
        # 421todo do not skip non-root bones if parent isnt skipped
        if not bone.use_deform:
            log.info('Skipping non-deform bone {} (intended for eg IK bones)', bone.name)
            skipped_bones.append(bone)
            continue
        bone_parents = bone.parent_recursive
        for skipped_bone in skipped_bones:
            if skipped_bone in bone_parents:
                log.error('bone {} has bone {} in its parents, but that bone was skipped', bone.name, skipped_bone.name)
        # make sure there is only one root bone
        root_parent_bone = bone_parents[-1] if bone_parents else bone
        if root_bone and root_parent_bone.name != root_bone.name:
            log.debug('bone_parents={!r} root_bone={!r} root_parent_bone={!r}', bone_parents, root_bone, root_parent_bone)
            log.error('armature {} has multiple root bones, at least {} and {}', armature.name, root_bone.name, root_parent_bone.name)
        root_bone = root_parent_bone
        
        # preserve ordering from armature
        if bone_parents:
            # from top parent to closest parent
            for parent in reversed(bone_parents):
                if parent not in bones_ordered:
                    bones_ordered.append(parent)
        bones_ordered.append(bone)
    
    return root_bone, bones_ordered

def write_armatures(file_write_skel, file_write_anim, scene, global_matrix, armatures, link_anim_basepath, link_bin_scale):
    log = getLogger('anim')

    # user_ variables store parameters (potentially) used by the script and to be restored later
    user_frame_current = scene.frame_current
    user_frame_subframe = scene.frame_subframe
    
    # 421todo force 20 fps somewhere?
    scene_fps = scene.render.fps / scene.render.fps_base
    
    # armatures is built in ObjexWriter#write_object in export_objex.py (look for self.armatures)
    for armature_name_q, armature, object_transform, armature_actions in armatures:
        if armature.animation_data:
            user_armature_action = armature.animation_data.action
        
        root_bone, bones_ordered = order_bones(armature)
        
        if not bones_ordered:
            # 421todo abort?
            log.error('armature {} has no bones', armature.name)
        
        if file_write_skel:
            write_skeleton(file_write_skel, global_matrix, object_transform, armature, armature_name_q, bones_ordered)
        
        if file_write_anim and armature_actions:
            if armature.animation_data:
                write_animations(file_write_anim, scene, global_matrix, object_transform, armature, armature_name_q, root_bone, bones_ordered, armature_actions, link_anim_basepath, link_bin_scale)
            else:
                log.warning(
                    'Skipped exporting actions {!r} with armature {},\n'
                    'because the armature did not have animation_data\n'
                    '(consider unchecking "Export all actions" under Objex armature properties;\n'
                    'if you do want actions to be exported with this armature,\n'
                    'animation_data can be initialized by creating a dummy action by adding a keyframe in pose mode)'
                    , armature_actions, armature.name
                )
        
        if armature.animation_data:
            armature.animation_data.action = user_armature_action
    
    scene.frame_set(user_frame_current, subframe=user_frame_subframe)

def write_animations(file_write_anim, scene, global_matrix, object_transform, armature, armature_name_q, root_bone, bones_ordered, actions, link_anim_basepath, link_bin_scale):
    log = getLogger('anim')
    fw = file_write_anim
    fw('# %s\n' % armature.name)

    if link_anim_basepath is not None and len(bones_ordered) != 21:
        log.warning('Requested exporting Link animation binary, but armature does not have 21 bones')
        link_anim_basepath = None

    for action in actions:
        data = armature.data.objex_bonus
        frame_start, frame_end = action.frame_range
        
        if data.start_frame_clamp == True:
            if frame_start < data.start_frame_clamp_value:
                frame_start = data.start_frame_clamp_value
        
        frame_count = int(frame_end - frame_start + 1)

        if data.end_frame_minus == True:
            frame_count = frame_count - data.end_frame_minus_value
        
        if frame_count <= 0:
            frame_start, frame_end = action.freame_range
            frame_count = 1

        fw('newanim %s %s %d\n' % (armature_name_q, util.quote(action.name), frame_count))

        link_anim_file = None
        if link_anim_basepath is not None:
            link_anim_filename = os.path.dirname(link_anim_basepath) + os.sep + str(frame_count) + '-' + action.name + '.bin'
            link_anim_file = open(link_anim_filename, 'wb')

        try:
            write_action(fw, scene, global_matrix, object_transform, armature, root_bone, bones_ordered, action, frame_start, frame_count, link_anim_file, link_bin_scale)
        finally:
            if link_anim_file is not None:
                link_anim_file.close()

        fw('\n')

    fw('\n')

def write_action(fw, scene, global_matrix, object_transform, armature, root_bone, bones_ordered, action, frame_start, frame_count, link_anim_file, link_bin_scale):
    log = getLogger('anim')
    transform = blender_version_compatibility.matmul(global_matrix, object_transform)
    transform3 = transform.to_3x3()
    transform3_inv = transform3.inverted()

    def link_write_shorts(x, y, z):
        link_anim_file.write(bytes([(x>>8)&0xFF, x&0xFF, (y>>8)&0xFF, y&0xFF, (z>>8)&0xFF, z&0xFF]))

    armature.animation_data.action = action
    
    pose_bones = armature.pose.bones
    root_pose_bone = pose_bones[root_bone.name]
    if link_anim_file is not None:
        eyes_bone = pose_bones.get('Eyes')
        mouth_bone = pose_bones.get('Mouth')
        if eyes_bone is None:
            log.warning('Eyes animation index bone not found (bone with name Eyes)')
        if mouth_bone is None:
            log.warning('Mouth animation index bone not found (bone with name Mouth)')

    if armature.location != mathutils.Vector((0,0,0)):
        log.debug('origin of armature {} {!r} is not world origin (0,0,0)', armature.name, armature.location)
    for child in armature.children:
        if child.location != armature.location:
            log.debug('origins of object {} {!r} and parent armature {} {!r} mismatch', child.name, child.location, armature.name, armature.location)
        if child.location != mathutils.Vector((0,0,0)):
            log.debug('origin of object {} {!r} (parent armature {}) is not world origin (0,0,0)', child.name, child.location, armature.name)

    # Iterate in case of wonky IK setup
    for i in range(10):
        scene.frame_set(int(frame_start))

    for frame_current_offset in range(frame_count):
        frame_current = frame_start + frame_current_offset
        scene.frame_set(int(frame_current))
        # 421todo what if root_bone.head != 0
        """
        > In .anim are the coordinates in loc x y z absolute or relative to the position of the root bone as defined in .skel ?
        > > I think it may be the case that loc x y z is relative to the world origin.
        so that's a TODO - check what happens with non-zero root bone
        if the root bone skeleton position is discarded it should be added in loc
        """
        """
        now we use #head and not #location (which was assumed to be a displacement in armature coordinates but it's not)
        the root bone loc will always be relative to armature
        so if root bone is not at 0,0,0 in edit mode (aka root_bone.head != 0) it may cause issues if loc and root_bone.head are summed
        """
        root_loc = root_pose_bone.head # armature space
        root_loc = blender_version_compatibility.matmul(transform, root_loc)
        fw('loc %.6f %.6f %.6f\n' % (root_loc.x, root_loc.y, root_loc.z)) # 421todo what about "ms"
        if link_anim_file is not None:
            x = int(root_loc.x * link_bin_scale)
            y = int(root_loc.y * link_bin_scale)
            z = int(root_loc.z * link_bin_scale)
            if any(n < -0x8000 or n > 0x7FFF for n in [x, y, z]):
                log.warning('Link anim position values out of range')
            link_write_shorts(x, y, z)
        #TODO
        for bone in bones_ordered:
            pose_bone = pose_bones[bone.name]
            parent_pose_bone = pose_bone.parent
            
            """
            pose_bone.matrix_channel is the deform matrix in armature space
            We use the deform relative to parent (parent_pose_bone.matrix_channel.inverted() * pose_bone.matrix_channel)
            
            Reference:
                OoT: Matrix_JointPosition source (decomp at 0x800d1340)
                Blender: eulO_to_mat3 source
            """
            
            # 421todo what if armature/object transforms are not identity?
            if parent_pose_bone:
                # we only care about the 3x3 rotation part
                # for rotations, .transposed() is the same as .inverted()
                rot_matrix = blender_version_compatibility.matmul(
                                parent_pose_bone.matrix_channel.to_3x3().transposed(),
                                pose_bone.matrix_channel.to_3x3())
            else:
                # without a parent, transform can stay relative to armature (as if parent_pose_bone.matrix_channel = Identity)
                rot_matrix = pose_bone.matrix_channel.to_3x3()
            rot_matrix = blender_version_compatibility.matmul(
                            blender_version_compatibility.matmul(transform3, rot_matrix),
                            transform3_inv)

            # OoT actually uses XYZ Euler angles.
            rotation_euler_zyx = rot_matrix.to_euler('XYZ')
            # 5 digits: precision of s16 angles in radians is 2pi/2^16 ~ ‭0.000096
            fw('rot %.5f %.5f %.5f\n' % (rotation_euler_zyx.x, rotation_euler_zyx.y, rotation_euler_zyx.z))
            if link_anim_file is not None:
                def rad_to_shortang(r):
                    r *= 0x8000 / math.pi
                    r = int(r) & 0xFFFF
                    if r >= 0x8000:
                        r -= 0x10000
                    return r
                link_write_shorts(rad_to_shortang(rotation_euler_zyx.x), rad_to_shortang(rotation_euler_zyx.y), rad_to_shortang(rotation_euler_zyx.z))
        
        if link_anim_file is not None:
            texanimvalue = 0
            if eyes_bone is not None:
                i = round(eyes_bone.head.x) # Want it in armature space, not transform space
                if i < -1 or i > 7:
                    log.warning('Link eye index (Eyes bone X value) out of range -1 to 7')
                    if i < -1 or i > 14:
                        i = -1
                texanimvalue |= i+1
            if mouth_bone is not None:
                i = round(mouth_bone.head.x)
                if i < -1 or i > 3:
                    log.warning('Link mouth index (Mouth bone X value) out of range -1 to 3')
                    if i < -1 or i > 14:
                        i = -1
                texanimvalue |= (i+1) << 4
            link_anim_file.write(texanimvalue.to_bytes(2, byteorder='big'))

from bpy_extras.io_utils import orientation_helper, axis_conversion
import os

@orientation_helper(axis_forward='-Z', axis_up="Y")
class OBJEX_OT_export_link_anim_bin(bpy.types.Operator):
    bl_idname = 'objex.export_link_anim_bin'
    bl_label = 'Export Link anim bin'
    bl_options = {'PRESET'}

    def execute(self, context):
        global_matrix = axis_conversion(to_forward=self.axis_forward, to_up=self.axis_up,).to_4x4()
        if context.object.type == "ARMATURE":
            armature = context.object
        else:
            armature = context.object.find_armature()
        assert armature is not None, getattr(context, "object", None)

        armature_name_q = '"armature"' # whatever
        object_transform = armature.matrix_world
        actions = list(bpy.data.actions) # todo?
        file_path = bpy.path.abspath(armature.data.objex_bonus.anim_filepath)
        print(file_path)
        def noop(*args, **kwargs):
            pass
        write_armatures(noop, noop, context.scene, global_matrix, [(armature_name_q, armature, object_transform, actions)], file_path, 1000)
        return {"FINISHED"}

import struct
from pathlib import Path

@orientation_helper(axis_forward='-Z', axis_up="Y")
class OBJEX_OT_import_link_anim_bin(bpy.types.Operator):
    bl_idname = 'objex.import_link_anim_bin'
    bl_label = 'Import Link anim bin'
    bl_options = {'PRESET'}

    def read_file(self, file) -> bytes:
        data_file = open(file, "rb")
        data = data_file.read()
        data_file.close()
        return data
    
    def read_file_size(self, file) -> int:
        data_stat = os.stat(file)
        return data_stat.st_size

    def binang_to_rad(self, rot):
        return rot * (math.pi / 0x8000)

    def execute(self, context):
        bone_name = [
            'fk_00', 'fk_01', 'fk_02', 'fk_03', 'fk_04', 'fk_05', 'fk_06', 'fk_07', 'fk_08',
            'fk_09', 'fk_10', 'fk_11', 'fk_12', 'fk_13', 'fk_14', 'fk_15', 'fk_16', 'fk_17',
            'fk_18', 'fk_19', 'fk_20'
        ]
        ik_bone = { 
            'HeadIK':      'limb_10',      
            'ROOT':        'limb_00',      
            'Sheath':      'limb_19',    

            'Hand.IK.L':    'limb_15',        
            'Hand.IK.R':    'limb_18',    
            'Hand.pole.IK.L':          'limb_13',
            'Hand.pole.IK.R':          'limb_16',

            'Leg.IK.L':            'limb_08',     
            'Leg.IK.R':            'limb_05',     
            'Leg.01.track.L.001':  'limb_06',
            'Leg.01.track.R.001':  'limb_03',
        }

        if context.object.type == "ARMATURE":
            armature = context.object
        else:
            armature = context.object.find_armature()

        file = bpy.path.abspath(armature.data.objex_bonus.src_bin_anim_filepath)
        data:bytes = self.read_file(file)
        size:int = self.read_file_size(file)
        # (rot + trans) * axis * sizeof(s16) + sizeof(s16)
        #                                      face anims
        axis_count = (21 + 1) * 3 + 1
        frame_size = axis_count * 2
        frame_num = int(size / frame_size)
        frame_data = struct.unpack('>'+'h' * int(size / 2), data)
        anim_name = name=Path(file).stem

        if armature.animation_data is None:
            armature.animation_data_create()
        
        if anim_name in bpy.data.actions:
            action = bpy.data.actions[anim_name]
        else:
            action = bpy.data.actions.new(anim_name)

        armature.animation_data.action = action

        root_bone, bones_ordered = order_bones(armature)

        bone_name = [bone.name for bone in bones_ordered]

        master = armature.pose.bones[bone_name[0]] #previously control.master
        master.location[0] = 0
        master.location[1] = 0
        master.location[2] = 0
        master.rotation_quaternion[0] = 1
        master.rotation_quaternion[1] = 0
        master.rotation_quaternion[2] = 0
        master.rotation_quaternion[1] = 0
        master.keyframe_insert(data_path='location', frame=0, group="Master")
        master.keyframe_insert(data_path='location', frame=0, group="Master")

        for i in range(frame_num):
            frame = frame_data[axis_count * i:]

            armature.pose.bones[bone_name[0]].location[0] = frame[0] / 1000
            armature.pose.bones[bone_name[0]].location[1] = frame[1] / 1000
            armature.pose.bones[bone_name[0]].location[2] = frame[2] / 1000

            for j in range(21):
                bone = armature.pose.bones[bone_name[j]]

                bone.rotation_mode = 'XYZ'
                bone.rotation_euler[0] = self.binang_to_rad(frame[3 + j * 3])
                bone.rotation_euler[1] = self.binang_to_rad(frame[3 + j * 3 + 1])
                bone.rotation_euler[2] = self.binang_to_rad(frame[3 + j * 3 + 2])
            
            eye_bone = armature.pose.bones.get('Eyes')
            mouth_bone = armature.pose.bones.get('Mouth')

            if eye_bone and mouth_bone:

                eye_bone.location[0] = -((frame[(21 + 1) * 3] & 0xF) - 1)
                mouth_bone.location[0] = -((frame[(21 + 1) * 3] >> 4) - 1)
                eye_bone.keyframe_insert(data_path='location', frame=i, group='Eye')
                mouth_bone.keyframe_insert(data_path='location', frame=i, group='Mouth')
        
            for name_control, name_source in ik_bone.items():
                bone_control = armature.pose.bones[name_control]
                bone_source = armature.pose.bones[name_source]

                constraint:bpy.types.CopyTransformsConstraint = bone_control.constraints.new(type='COPY_TRANSFORMS')
                constraint.target = armature
                constraint.subtarget = bone_source.name

                # 5Head Hacks
                context_copy = context.copy()
                context_copy['active_pose_bone'] = bone_control 
                context_copy['active_object'] = armature
                context_copy['object'] = armature

                bpy.ops.constraint.apply(
                    context_copy,
                    constraint=constraint.name, 
                    owner='BONE'
                )
                bone_control.keyframe_insert(data_path='location', frame=i, group=name_control)
                bone_control.keyframe_insert(data_path='rotation_quaternion', frame=i, group=name_control)
        
        # bpy.ops.action.clean(context_copy, channels=True)

        return {"FINISHED"}
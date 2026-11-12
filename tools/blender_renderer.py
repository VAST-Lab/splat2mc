bl_info = {
    "name": "Spherical Orthographic Imposter Renderer",
    "blender": (3, 0, 0),
    "category": "Render",
}

import bpy
import os
import math


class RENDER_OT_imposter_array(bpy.types.Operator):
    bl_idname = "render.imposter_array"
    bl_label = "Render Spherical Imposter Array"

    def execute(self, context):
        scene = context.scene
        target = context.active_object
        cols = scene.imp_cols
        rows = scene.imp_rows

        if not target:
            self.report({'ERROR'}, "Select the target object you want to render.")
            return {'CANCELLED'}

        output_dir = bpy.path.abspath(scene.imp_path)
        os.makedirs(output_dir, exist_ok=True)

        if not scene.camera:
            bpy.ops.object.camera_add()
            scene.camera = context.active_object

        cam = scene.camera
        # Use Orthographic camera to prevent perspective distortion jumps between frames
        cam.data.type = 'ORTHO'
        cam.data.ortho_scale = scene.imp_ortho_scale

        scene.render.resolution_x = scene.imp_res
        scene.render.resolution_y = scene.imp_res
        scene.render.film_transparent = True

        radius = scene.imp_radius
        pitch_min = scene.imp_pitch_min
        pitch_max = scene.imp_pitch_max

        count = 0
        yaw_step = 360.0 / cols
        pitch_step = (pitch_max - pitch_min) / max(1, (rows - 1))

        # Row 0 = Top elevation (pitch_max), Row N-1 = Bottom elevation (pitch_min)
        for r_idx in range(rows):
            pitch_deg = pitch_max - (r_idx * pitch_step)
            pitch_rad = math.radians(pitch_deg)

            horiz_radius = radius * math.cos(pitch_rad)
            height = radius * math.sin(pitch_rad)

            for c_idx in range(cols):
                yaw_deg = c_idx * yaw_step
                yaw_rad = math.radians(yaw_deg)

                cam.location = (
                    target.location.x + math.cos(yaw_rad) * horiz_radius,
                    target.location.y + math.sin(yaw_rad) * horiz_radius,
                    target.location.z + height
                )

                direction = target.location - cam.location
                cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
                bpy.context.view_layer.update()

                filepath = os.path.join(output_dir, f"image_{str(count).zfill(3)}.png")
                scene.render.filepath = filepath

                # Support standard Blender render or 3DGS plugin render operator
                if hasattr(bpy.ops, "sna") and hasattr(bpy.ops.sna, "dgs_render_advanced_render_ba196"):
                    bpy.ops.sna.dgs_render_advanced_render_ba196()
                else:
                    bpy.ops.render.render(write_still=True)

                count += 1

        self.report({'INFO'}, f"Rendered {count} spherical frames ({cols}x{rows}).")
        return {'FINISHED'}


class VIEW3D_PT_imposter_array(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Imposter Array"
    bl_label = "Spherical Imposter Settings"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.prop(scene, "imp_res", text="Tile Resolution")
        layout.prop(scene, "imp_cols", text="Columns (Yaw)")
        layout.prop(scene, "imp_rows", text="Rows (Pitch)")
        layout.separator()
        layout.prop(scene, "imp_radius", text="Sphere Radius")
        layout.prop(scene, "imp_ortho_scale", text="Ortho Scale (Size)")
        layout.prop(scene, "imp_pitch_max", text="Max Pitch (Top)")
        layout.prop(scene, "imp_pitch_min", text="Min Pitch (Bottom)")
        layout.separator()
        layout.prop(scene, "imp_path", text="Output Folder")
        layout.operator("render.imposter_array", icon='CAMERA_DATA')


def register():
    bpy.utils.register_class(RENDER_OT_imposter_array)
    bpy.utils.register_class(VIEW3D_PT_imposter_array)
    bpy.types.Scene.imp_res = bpy.props.IntProperty(default=128, min=32, max=1024)
    bpy.types.Scene.imp_cols = bpy.props.IntProperty(default=32, min=4, max=64)
    bpy.types.Scene.imp_rows = bpy.props.IntProperty(default=5, min=1, max=16)
    bpy.types.Scene.imp_radius = bpy.props.FloatProperty(default=5.0, min=0.5)
    bpy.types.Scene.imp_ortho_scale = bpy.props.FloatProperty(default=4.0, min=0.1)
    bpy.types.Scene.imp_pitch_max = bpy.props.FloatProperty(default=45.0, min=0.0, max=85.0)
    bpy.types.Scene.imp_pitch_min = bpy.props.FloatProperty(default=-45.0, min=-85.0, max=0.0)
    bpy.types.Scene.imp_path = bpy.props.StringProperty(subtype='DIR_PATH', default="//renders/")


def unregister():
    bpy.utils.unregister_class(RENDER_OT_imposter_array)
    bpy.utils.unregister_class(VIEW3D_PT_imposter_array)


if __name__ == "__main__":
    register()
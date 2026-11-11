bl_info = {
    "name": "Imposter Renderer",
    "blender": (3, 0, 0),
    "category": "Render",
}

import bpy
import os
import math

class RENDER_OT_imposter_array(bpy.types.Operator):
    bl_idname = "render.imposter_array"
    bl_label = "Render Imposter Array"
    
    def execute(self, context):
        scene = context.scene
        target = context.active_object
        cols = scene.imp_cols
        
        if not target:
            self.report({'ERROR'}, "Select the object you want to render.")
            return {'CANCELLED'}

        output_dir = bpy.path.abspath(scene.imp_path)
        if not os.path.exists(output_dir): os.makedirs(output_dir)

        if not scene.camera:
            bpy.ops.object.camera_add()
            scene.camera = context.active_object
        cam = scene.camera

        scene.render.resolution_x = scene.imp_res
        scene.render.resolution_y = scene.imp_res

        # Row configuration: [Height, Radius]
        rows = [
            (scene.imp_h_top, scene.imp_r_top),    # Row 0
            (0.0, scene.imp_r_mid),               # Row 1
            (scene.imp_h_bot, scene.imp_r_bot)     # Row 2
        ]

        count = 0
        degree_step = 360.0 / cols

        for r_idx, (height, radius) in enumerate(rows):
            for c_idx in range(cols):
                angle = math.radians(c_idx * degree_step)
                
                cam.location = (
                    math.cos(angle) * radius,
                    math.sin(angle) * radius,
                    height
                )
                
                direction = target.location - cam.location
                cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
                
                bpy.context.view_layer.update()
                
                scene.render.filepath = os.path.join(output_dir, f"render_{str(count).zfill(3)}.png")
                
                bpy.context.view_layer.update()
                bpy.data.scenes["Scene"].render.filepath = os.path.join(output_dir, f"render_{str(count).zfill(3)}.png")
                bpy.ops.sna.dgs_render_advanced_render_ba196()
                
                count += 1
                
        self.report({'INFO'}, f"Rendered {count} frames ({cols} columns).")
        return {'FINISHED'}

class VIEW3D_PT_imposter_array(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Imposter Array"
    bl_label = "Imposter Array Settings"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        layout.prop(scene, "imp_res", text="Resolution")
        layout.prop(scene, "imp_cols", text="Columns (Angles)")
        layout.separator()
        
        box = layout.box()
        box.label(text="Top Row (Row 0)")
        box.prop(scene, "imp_r_top", text="Radius")
        box.prop(scene, "imp_h_top", text="Height")
        
        box = layout.box()
        box.label(text="Eye Level (Row 1)")
        box.prop(scene, "imp_r_mid", text="Radius")
        
        box = layout.box()
        box.label(text="Bottom Row (Row 2)")
        box.prop(scene, "imp_r_bot", text="Radius")
        box.prop(scene, "imp_h_bot", text="Height")
        
        layout.prop(scene, "imp_path", text="Folder")
        layout.operator("render.math_imposter", icon='CAMERA_DATA')

def register():
    bpy.utils.register_class(RENDER_OT_imposter_array)
    bpy.utils.register_class(VIEW3D_PT_imposter_array)
    bpy.types.Scene.imp_res = bpy.props.IntProperty(default=256)
    bpy.types.Scene.imp_cols = bpy.props.IntProperty(default=16, min=4, max=64)
    bpy.types.Scene.imp_path = bpy.props.StringProperty(subtype='DIR_PATH')
    bpy.types.Scene.imp_r_top = bpy.props.FloatProperty(default=3.0)
    bpy.types.Scene.imp_h_top = bpy.props.FloatProperty(default=2.0)
    bpy.types.Scene.imp_r_mid = bpy.props.FloatProperty(default=5.0)
    bpy.types.Scene.imp_r_bot = bpy.props.FloatProperty(default=3.0)
    bpy.types.Scene.imp_h_bot = bpy.props.FloatProperty(default=-1.0)

if __name__ == "__main__":
    register()
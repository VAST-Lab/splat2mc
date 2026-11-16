"""CLI interface for splat2mc."""

import sys
import click
from pathlib import Path
from .converter import convert_ply_to_datapack, load_ply

_TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))


@click.group()
@click.version_option()
def main():
    """Convert 3D Gaussian Splats to Minecraft Java datapacks and Bedrock Add-Ons."""
    pass


@main.command()
@click.argument("ply_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-o", "--output",
    type=click.Path(path_type=Path),
    default=Path("./output"),
    help="Output directory for datapack",
)
@click.option(
    "-n", "--max-particles",
    type=int,
    default=5000,
    help="Maximum number of particles (default: 5000)",
)
@click.option(
    "-s", "--size",
    type=float,
    default=10.0,
    help="Target size in Minecraft blocks (default: 10)",
)
@click.option(
    "--min-opacity",
    type=float,
    default=0.1,
    help="Minimum opacity threshold (default: 0.1)",
)
@click.option(
    "-p", "--particle-scale",
    type=float,
    default=1.0,
    help="Multiplier for individual particle sizes (default: 1.0)",
)
@click.option(
    "-t", "--particle-type",
    type=click.Choice(["dust", "entity_effect", "dust_color_transition"]),
    default="dust",
    help="Minecraft particle type to use (default: dust)",
)
@click.option(
    "--flip-y",
    is_flag=True,
    help="Flip the Y-axis (up vector) to fix upside-down imports",
)
def convert(
    ply_file: Path,
    output: Path,
    max_particles: int,
    size: float,
    min_opacity: float,
    particle_scale: float,
    particle_type: str,
    flip_y: bool,
):
    """Convert a PLY file to a Minecraft datapack."""
    output.mkdir(parents=True, exist_ok=True)
    datapack_path = convert_ply_to_datapack(
        ply_path=ply_file,
        output_dir=output,
        max_particles=max_particles,
        target_size=size,
        min_opacity=min_opacity,
        particle_scale_multiplier=particle_scale,
        particle_type=particle_type,
        flip_y=flip_y,
    )
    click.echo(f"\n✓ Datapack created: {datapack_path}")


@main.command()
@click.argument("ply_file", type=click.Path(exists=True, path_type=Path))
def info(ply_file: Path):
    """Show info about a PLY file."""
    splats = load_ply(ply_file)
    if not splats:
        click.echo("No splats found in file.")
        return

    opacities = [s.opacity for s in splats]
    scales = [s.scale for s in splats]
    click.echo(f"File: {ply_file}")
    click.echo(f"Splats: {len(splats):,}")
    click.echo(f"Opacity: min={min(opacities):.3f}, max={max(opacities):.3f}, avg={sum(opacities)/len(opacities):.3f}")
    click.echo(f"Scale: min={min(scales):.6f}, max={max(scales):.6f}, avg={sum(scales)/len(scales):.6f}")


@main.command()
@click.argument("splat_dir", type=click.Path(exists=True, path_type=Path))
@click.option("-o", "--output", type=click.Path(path_type=Path), default=Path("./output"))
@click.option("-n", "--max-particles", type=int, default=5000)
def batch(splat_dir: Path, output: Path, max_particles: int):
    """Convert all PLY files in a directory."""
    ply_files = list(splat_dir.glob("*.ply"))
    if not ply_files:
        click.echo(f"No PLY files found in {splat_dir}")
        return
    output.mkdir(parents=True, exist_ok=True)
    for ply_file in ply_files:
        convert_ply_to_datapack(ply_path=ply_file, output_dir=output, max_particles=max_particles)


@main.command("render-imposter")
@click.argument("ply_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "-o", "--output",
    type=click.Path(path_type=Path),
    default=Path("./bedrock/01_Imposter_Pack/Imposter_RP/textures/entity/spritesheet_1.png"),
    help="Output spherical spritesheet PNG path",
)
@click.option("--icon-out", type=click.Path(path_type=Path), default=None, help="Optional 128x128 item icon PNG path")
@click.option("-c", "--cols", type=int, default=16, help="Yaw columns (default: 16)")
@click.option("-r", "--rows", type=int, default=5, help="Pitch rows (default: 5)")
@click.option("--res", type=int, default=144, help="Tile resolution in px (default: 144)")
@click.option("--hue-shift", type=float, default=0.0, help="Hue shift in degrees")
def render_imposter_cmd(
    ply_file: Path,
    output: Path,
    icon_out: Path | None,
    cols: int,
    rows: int,
    res: int,
    hue_shift: float,
):
    """Render a 3DGS PLY into a Bedrock spherical imposter spritesheet (no Blender needed)."""
    from render_imposter import GaussianScene, render_imposter_atlas

    scene = GaussianScene(ply_file)
    render_imposter_atlas(
        scene,
        output_atlas=output,
        output_icon=icon_out,
        cols=cols,
        rows=rows,
        tile_res=res,
        hue_shift_deg=hue_shift,
    )


@main.command("export-mesh")
@click.argument("ply_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--geo-out",
    type=click.Path(path_type=Path),
    default=Path("./bedrock/02_Poly_Mesh_Pack/PolyMesh_RP/models/entity/poly_gem_0.geo.json"),
    help="Output Bedrock .geo.json path",
)
@click.option(
    "--tex-out",
    type=click.Path(path_type=Path),
    default=Path("./bedrock/02_Poly_Mesh_Pack/PolyMesh_RP/textures/entity/poly_gem_tex_0.png"),
    help="Output baked RGBA 6-view texture atlas path",
)
@click.option(
    "-m", "--mode",
    type=click.Choice(["smooth", "detailed", "faceted"]),
    default="smooth",
    help="Shrinkwrap mesh style (smooth=1280f, detailed=5120f, faceted=320f)",
)
@click.option("-s", "--size", type=float, default=24.0, help="Model size in Bedrock units (16 = 1 block)")
@click.option("--hue-shift", type=float, default=0.0, help="Hue shift in degrees")
def export_mesh_cmd(
    ply_file: Path,
    geo_out: Path,
    tex_out: Path,
    mode: str,
    size: float,
    hue_shift: float,
):
    """Shrinkwrap 3DGS surface map & 6-view studio texture bake to Bedrock poly_mesh."""
    from export_mesh import export_shrinkwrap_polymesh

    export_shrinkwrap_polymesh(
        ply_file,
        output_geo=geo_out,
        output_tex=tex_out,
        mode=mode,
        target_size=size,
        hue_shift_deg=hue_shift,
    )


@main.command("build-gallery")
@click.option(
    "--pack",
    type=click.Choice(["all", "01", "02", "03", "imposter", "polymesh", "splat"]),
    default="all",
    help="Which Bedrock pack to build (default: all)",
)
def build_gallery_cmd(pack: str):
    """Build the 3-tier Bedrock Gem packs (01_Imposter_Pack, 02_Poly_Mesh_Pack, 03_Full_GS_Pack)."""
    import sys as _sys
    from build_gallery import main as _bg_main

    _sys.argv = ["build_gallery.py", "--pack", pack]
    _bg_main()


if __name__ == "__main__":
    main()

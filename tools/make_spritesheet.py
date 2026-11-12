#!/usr/bin/env python3
import argparse
from pathlib import Path
from PIL import Image


def create_spritesheet(input_dir: Path, output_file: Path, rows: int = 5):
    files = [
        f for f in input_dir.iterdir()
        if f.suffix.lower() == ".png" and f.name != output_file.name
    ]
    files.sort(key=lambda f: int("".join(filter(str.isdigit, f.stem)) or 0))

    total_frames = len(files)
    if total_frames == 0:
        raise FileNotFoundError(f"No PNG frames found in {input_dir}")

    if total_frames % rows != 0:
        print(f"Warning: {total_frames} frames is not evenly divisible by {rows} rows.")

    cols = total_frames // rows
    with Image.open(files[0]) as first_img:
        w, h = first_img.size

    print(f"Building {cols}x{rows} atlas ({w * cols}x{h * rows} px) from {total_frames} frames...")
    spritesheet = Image.new("RGBA", (w * cols, h * rows), (0, 0, 0, 0))

    for index, filepath in enumerate(files):
        col = index % cols
        row = index // cols
        with Image.open(filepath) as frame:
            spritesheet.paste(frame.resize((w, h)), (col * w, row * h))

    output_file.parent.mkdir(parents=True, exist_ok=True)
    spritesheet.save(output_file)
    print(f"✓ Saved spritesheet to {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Combine rendered frames into a Bedrock imposter atlas.")
    parser.add_argument("-i", "--input-dir", type=Path, default=Path("./spritesheet"), help="Directory containing rendered PNG frames")
    parser.add_argument("-o", "--output", type=Path, default=Path("./bedrock/01_Imposter_Pack/Imposter_RP/textures/entity/spritesheet.png"), help="Output spritesheet path")
    parser.add_argument("-r", "--rows", type=int, default=5, help="Number of pitch rows")
    args = parser.parse_args()
    create_spritesheet(args.input_dir, args.output, args.rows)
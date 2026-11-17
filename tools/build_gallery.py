#!/usr/bin/env python3
"""
Compatibility entrypoint for `splat2mc build-gallery`.

Delegates to the modular per-pack builders:
- `tools/build_imposter_pack.py` (`01_SplatImposter_Pack`)
- `tools/build_mesh_pack.py` (`02_SplatMesh_Pack`)
- `tools/build_splat_pack.py` (`03_SplatRenderer_Pack`)
- `tools/build_all_packs.py` (automated runner)
"""
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from build_all_packs import main  # noqa: F401
from build_imposter_pack import build_imposter_pack  # noqa: F401
from build_mesh_pack import build_mesh_pack  # noqa: F401
from build_splat_pack import build_splat_pack  # noqa: F401
from pack_utils import generate_pack_script_js  # noqa: F401


if __name__ == "__main__":
    main()

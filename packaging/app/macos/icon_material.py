"""Helper-family native icon material, copied from OH's approved generator.

Only the light tile renderer is retained. No OptionHelper artwork, CLI, runtime
imports or file writes are inherited. ResearchHelper supplies its own SVG mark.
"""
from pathlib import Path
import os
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

CANVAS_SIZE = 1024
TILE_X = TILE_Y = 112
TILE_SIZE = 800
TILE_RADIUS = 176
TILE_SHEEN_CENTER = (0.50, 0.43)
TILE_SHEEN_RADIUS = 0.86
TileTheme = SimpleNamespace
LIGHT_TILE = TileTheme(
    gradient_start=(252, 253, 251), gradient_end=(241, 242, 239),
    sheen_color=(255, 255, 255), sheen_strength=0.14,
    edge=(210, 212, 207, 97), highlight=(255, 255, 255, 122),
    shadow_color=(36, 39, 45), shadow_opacity=0.10,
    small_gradient_end=((249, 250, 248), (247, 248, 245), (244, 245, 242)),
    small_sheen=(0.10, 0.12, 0.14),
)


def _supersample(size: int) -> int:
    if size <= 64:
        return 8
    if size <= 256:
        return 4
    return 2


def _tile_material(theme: TileTheme, size: int) -> tuple[tuple[int, int, int], float, float, float, float, float]:
    """Return small-size-aware tile finish parameters in output pixels."""
    if size == 16:
        return theme.small_gradient_end[0], 0.45, 0.25, theme.shadow_opacity * .8, 0.5, theme.small_sheen[0]
    if size == 32:
        return theme.small_gradient_end[1], 0.7, 0.4, theme.shadow_opacity * .9, 0.6, theme.small_sheen[1]
    if size == 64:
        return theme.small_gradient_end[2], 1.0, 0.65, theme.shadow_opacity, 0.75, theme.small_sheen[2]
    scale = size / CANVAS_SIZE
    return (
        theme.gradient_end,
        16 * scale,
        8 * scale,
        theme.shadow_opacity,
        max(0.8, 2.5 * scale),
        theme.sheen_strength,
    )


def _draw_tile_material(canvas: Image.Image, *, theme: TileTheme, size: int, supersample: int, user_scale: float) -> None:
    (
        gradient_end,
        shadow_blur,
        shadow_offset,
        shadow_opacity,
        edge_width,
        sheen_strength,
    ) = _tile_material(theme, size)
    bounds = (
        TILE_X * user_scale,
        TILE_Y * user_scale,
        (TILE_X + TILE_SIZE) * user_scale,
        (TILE_Y + TILE_SIZE) * user_scale,
    )
    radius = TILE_RADIUS * user_scale

    shadow_mask = Image.new("L", canvas.size, 0)
    shadow_draw = ImageDraw.Draw(shadow_mask)
    offset = shadow_offset * supersample
    shadow_draw.rounded_rectangle(
        (bounds[0], bounds[1] + offset, bounds[2], bounds[3] + offset),
        radius=radius,
        fill=255,
    )
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(shadow_blur * supersample))
    shadow_alpha = np.rint(np.asarray(shadow_mask, dtype=np.float32) * shadow_opacity).astype(np.uint8)
    shadow_layer = Image.new("RGBA", canvas.size, (*theme.shadow_color, 0))
    shadow_layer.putalpha(Image.fromarray(shadow_alpha, "L"))
    canvas.alpha_composite(shadow_layer)

    work_size = canvas.size[0]
    x_user = (np.arange(work_size, dtype=np.float32) + 0.5) / user_scale
    y_user = (np.arange(work_size, dtype=np.float32) + 0.5) / user_scale
    x_t = np.clip((x_user - TILE_X) / TILE_SIZE, 0.0, 1.0)
    y_t = np.clip((y_user - TILE_Y) / TILE_SIZE, 0.0, 1.0)
    t = (x_t[np.newaxis, :] + y_t[:, np.newaxis]) / 2
    start = np.asarray(theme.gradient_start, dtype=np.float32)
    end = np.asarray(gradient_end, dtype=np.float32)
    rgb = np.rint(start + t[:, :, np.newaxis] * (end - start)).astype(np.uint8)
    # A broad upper-left highlight gives the tile a shallow material plane;
    # it is intentionally too soft to compete with the red focal mark.
    sheen_distance = np.sqrt(
        (x_t[np.newaxis, :] - TILE_SHEEN_CENTER[0]) ** 2
        + (y_t[:, np.newaxis] - TILE_SHEEN_CENTER[1]) ** 2
    ) / TILE_SHEEN_RADIUS
    sheen = np.clip(1.0 - sheen_distance, 0.0, 1.0) ** 1.5 * sheen_strength
    sheen_rgb = np.asarray(theme.sheen_color, dtype=np.float32)
    rgb = np.rint(rgb + (sheen_rgb - rgb) * sheen[:, :, np.newaxis]).astype(np.uint8)
    tile_layer = Image.fromarray(rgb, "RGB").convert("RGBA")
    tile_mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(tile_mask).rounded_rectangle(bounds, radius=radius, fill=255)
    tile_layer.putalpha(tile_mask)
    canvas.alpha_composite(tile_layer)

    finish = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    finish_draw = ImageDraw.Draw(finish)
    edge_work = max(1, round(edge_width * supersample))
    edge_inset = edge_work / 2
    finish_draw.rounded_rectangle(
        (
            bounds[0] + edge_inset,
            bounds[1] + edge_inset,
            bounds[2] - edge_inset,
            bounds[3] - edge_inset,
        ),
        radius=max(0, radius - edge_inset),
        outline=theme.edge,
        width=edge_work,
    )
    highlight_width = max(1, round(max(0.5, edge_width * 0.65) * supersample))
    highlight_inset = edge_work * 1.5
    finish_draw.rounded_rectangle(
        (
            bounds[0] + highlight_inset,
            bounds[1] + highlight_inset,
            bounds[2] - highlight_inset,
            bounds[3] - highlight_inset,
        ),
        radius=max(0, radius - highlight_inset),
        outline=theme.highlight,
        width=highlight_width,
    )
    canvas.alpha_composite(finish)


def render_icon_assets(release=None):
    """Use the shared native material and the unchanged, browser-rendered SVG mark."""
    root = Path(__file__).resolve().parents[3]
    release = Path(release or os.environ.get('RESEARCHHELPER_RELEASE_OUTPUT') or root/'result/build-staging/current').resolve()
    staging = root / 'result/build-staging'
    if not release.is_relative_to(staging) or release == staging:
        raise ValueError('Icon output must remain inside ResearchHelper build staging')
    names = ((16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"),
             (128, "128x128"), (256, "128x128@2x"), (256, "256x256"),
             (512, "256x256@2x"), (512, "512x512"), (1024, "512x512@2x"))
    for size, name in names:
        supersample = _supersample(size)
        work_size = size * supersample
        canvas = Image.new("RGBA", (work_size, work_size), (0, 0, 0, 0))
        _draw_tile_material(canvas, theme=LIGHT_TILE, size=size, supersample=supersample,
                            user_scale=work_size / CANVAS_SIZE)
        with Image.open(release / "mark-renders" / f"mark-{size}.png") as mark:
            if mark.size != canvas.size:
                raise ValueError("ResearchHelper mark has the wrong supersampled size")
            canvas.alpha_composite(mark.convert("RGBA"))
        canvas = canvas.resize((size, size), Image.Resampling.LANCZOS)
        canvas.save(release / "ResearchHelper.iconset" / f"icon_{name}.png", compress_level=9)

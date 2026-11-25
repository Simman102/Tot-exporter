"""Programmatic tray and window icon generation for Tot-exporter."""

from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw


def create_app_icon(size: int = 64, active: bool = True) -> Image.Image:
    """Create a crisp Tot-exporter badge icon using Pillow."""
    scale = 4
    canvas_size = size * scale
    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = int(canvas_size * 0.06)
    radius = int(canvas_size * 0.22)
    bg_color = (28, 34, 48, 255)
    border_color = (72, 187, 178, 255) if active else (140, 148, 165, 255)

    draw.rounded_rectangle(
        [pad, pad, canvas_size - pad, canvas_size - pad],
        radius=radius,
        fill=bg_color,
        outline=border_color,
        width=max(4, canvas_size // 20),
    )

    # Stylized chat bubble inside badge
    bx0 = int(canvas_size * 0.22)
    by0 = int(canvas_size * 0.24)
    bx1 = int(canvas_size * 0.78)
    by1 = int(canvas_size * 0.68)
    bubble_radius = int(canvas_size * 0.10)
    bubble_color = (242, 193, 78, 255)

    draw.rounded_rectangle([bx0, by0, bx1, by1], radius=bubble_radius, fill=bubble_color)

    # Chat bubble tail
    tail = [
        (int(canvas_size * 0.32), by1 - 2),
        (int(canvas_size * 0.26), int(canvas_size * 0.80)),
        (int(canvas_size * 0.46), by1 - 2),
    ]
    draw.polygon(tail, fill=bubble_color)

    # Three chat lines inside bubble
    line_color = bg_color
    line_h = max(4, canvas_size // 22)
    for idx, frac in enumerate((0.78, 0.56)):
        ly = int(by0 + (by1 - by0) * (0.34 + idx * 0.30))
        lx0 = int(bx0 + (bx1 - bx0) * 0.16)
        lx1 = int(bx0 + (bx1 - bx0) * frac)
        draw.rounded_rectangle(
            [lx0, ly - line_h // 2, lx1, ly + line_h // 2],
            radius=line_h // 2,
            fill=line_color,
        )

    return img.resize((size, size), Image.Resampling.LANCZOS)


def save_ico(target_path: Path) -> Path:
    """Save a multi-resolution ``.ico`` file for Windows executables and windows."""
    base = create_app_icon(256, active=True)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    base.save(
        target_path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return target_path

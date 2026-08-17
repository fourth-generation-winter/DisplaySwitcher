# -*- coding: utf-8 -*-
"""Generate DisplaySwitcher.ico — blue tech HUD style matching the app UI.

Visual direction based on the in-app icon reference: rounded square, deep blue
technology gradient background, light-blue HUD monitor/window outline with a
glowing indicator dot at the top-right corner. Multi-size PNG-in-ICO for crisp
rendering at 16/24/32/48/64/128/256 px.
"""
import os
import struct
from io import BytesIO
from PIL import Image, ImageDraw, ImageFilter


def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def radial_gradient(size, center_c, edge_c):
    """Create a radial RGBA gradient image."""
    c0 = hex_to_rgb(center_c)
    c1 = hex_to_rgb(edge_c)
    cx = cy = size / 2.0
    max_d = (size / 2.0) * 1.15
    img = Image.new('RGBA', (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / max_d
            d = min(1.0, max(0.0, d))
            r = int(c0[0] + (c1[0] - c0[0]) * d)
            g = int(c0[1] + (c1[1] - c0[1]) * d)
            b = int(c0[2] + (c1[2] - c0[2]) * d)
            px[x, y] = (r, g, b, 255)
    return img


def draw_icon(size):
    """Draw the DisplaySwitcher icon at the requested size."""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))

    margin = max(2, size // 14)
    radius = size // 5
    x0, y0 = margin, margin
    x1, y1 = size - margin, size - margin

    # 背景：深蓝到科技蓝的径向渐变（大尺寸圆角 + 透明角）
    if size >= 64:
        bg = radial_gradient(size, '#0066CC', '#001A33')
        mask = Image.new('L', (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
        img.paste(bg, (0, 0), mask)
    else:
        # 小尺寸（标题栏/任务栏）改为整张不透明方形：去掉透明圆角，
        # 避免深色背景下圆角抗锯齿像素显示为白色毛边
        bg = Image.new('RGBA', (size, size), '#004C99')
        img.paste(bg, (0, 0))

    draw = ImageDraw.Draw(img)

    # 显示器/窗口轮廓：亮青蓝色 HUD 线条
    inner_margin = size // 5
    inner_radius = size // 8
    ix0, iy0 = inner_margin, inner_margin + size // 28
    ix1, iy1 = size - inner_margin, size - inner_margin - size // 28
    line = max(2, size // 28)
    glow_color = (100, 220, 255, 255)
    outline_color = (220, 245, 255, 255)

    # 大图标加辉光层
    if size >= 64:
        glow = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        gdraw.rounded_rectangle([ix0, iy0, ix1, iy1], radius=inner_radius,
                                outline=glow_color, width=line * 3)
        glow = glow.filter(ImageFilter.GaussianBlur(radius=max(1, size // 16)))
        img = Image.alpha_composite(img, glow)
        draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([ix0, iy0, ix1, iy1], radius=inner_radius,
                            outline=outline_color, width=line)

    # 右上角状态指示灯
    dot_r = max(2, size // 18)
    dot_x = x1 - radius // 2 - dot_r
    dot_y = y0 + radius // 2 + dot_r
    if size >= 64:
        glow_dot = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow_dot)
        gd.ellipse([dot_x - dot_r * 2, dot_y - dot_r * 2,
                    dot_x + dot_r * 2, dot_y + dot_r * 2],
                   fill=(80, 220, 255, 160))
        glow_dot = glow_dot.filter(ImageFilter.GaussianBlur(radius=max(1, size // 18)))
        img = Image.alpha_composite(img, glow_dot)
        draw = ImageDraw.Draw(img)

    draw.ellipse([dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r],
                   fill=(240, 250, 255, 255), outline=(80, 220, 255, 255),
                   width=max(1, line // 2))

    return img


def build_ico(images):
    """Assemble multiple PNG frames into a Windows ICO file."""
    buf = BytesIO()
    count = len(images)
    buf.write(struct.pack('<HHH', 0, 1, count))  # ICONDIR

    png_data = []
    for img in images:
        frame = BytesIO()
        img.convert('RGBA').save(frame, format='PNG', optimize=True)
        png_data.append(frame.getvalue())

    data_offset = 6 + 16 * count
    for data in png_data:
        img = Image.open(BytesIO(data))
        w_byte = img.width if img.width < 256 else 0
        h_byte = img.height if img.height < 256 else 0
        buf.write(struct.pack('<BBBBHHII',
                              w_byte, h_byte,  # width, height
                              0,               # colors (0 = >256)
                              0,               # reserved
                              1,               # color planes
                              32,              # bits per pixel
                              len(data),       # size in bytes
                              data_offset))    # offset to data
        data_offset += len(data)

    for data in png_data:
        buf.write(data)

    return buf.getvalue()


if __name__ == '__main__':
    sizes = [16, 24, 32, 48, 64, 128, 256]

    # 优先使用已审定的 icon-preview-256.png 作为母版；不存在则回退到矢量绘制
    preview_png = r'E:\WorkBuddy Project\2026-08-16-23-52-57\display-switcher\app\dist\icon-preview-256.png'
    if os.path.exists(preview_png):
        base = Image.open(preview_png).convert('RGBA')
        images = [base.resize((s, s), Image.LANCZOS) if s != 256 else base.copy() for s in sizes]
        print('Using source:', preview_png)
    else:
        images = [draw_icon(s) for s in sizes]
        print('Source PNG not found, falling back to vector draw.')

    out_ico = r'E:\WorkBuddy Project\2026-08-16-23-52-57\display-switcher\app\webroot\assets\DisplaySwitcher.ico'
    with open(out_ico, 'wb') as f:
        f.write(build_ico(images))
    print('Saved ICO:', out_ico)

    out_png = r'E:\WorkBuddy Project\2026-08-16-23-52-57\display-switcher\app\webroot\assets\icon-preview.png'
    images[-1].save(out_png)
    print('Saved PNG preview:', out_png)

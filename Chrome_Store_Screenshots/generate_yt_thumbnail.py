"""
Generate YouTube thumbnail (1280x720) for Stock Agent demo video.
Matches the existing brand palette from generate_promo_tiles.py.
"""

from PIL import Image, ImageDraw, ImageFont
import os

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

# Brand palette (matching existing promo tiles)
BG_GRADIENT_TOP = (14, 28, 52)
BG_GRADIENT_BOT = (6, 10, 16)
ACCENT = (47, 156, 240)
ACCENT_GLOW = (30, 120, 220)
TEXT_WHITE = (245, 248, 252)
TEXT_LIGHT = (200, 215, 230)
MUTED = (120, 145, 170)
GREEN = (62, 207, 142)
YELLOW = (240, 190, 60)
RED = (235, 85, 85)


def get_font(size, bold=False):
    """Get system font with fallback."""
    if bold:
        paths = [
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/calibrib.ttf",
        ]
    else:
        paths = [
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
        ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def draw_gradient_bg(img, top_color, bot_color):
    """Smooth vertical gradient fill."""
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for y in range(h):
        t = y / h
        r = int(top_color[0] * (1 - t) + bot_color[0] * t)
        g = int(top_color[1] * (1 - t) + bot_color[1] * t)
        b = int(top_color[2] * (1 - t) + bot_color[2] * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))


def draw_glow_circle(img, cx, cy, radius, color, alpha=40):
    """Soft radial glow effect."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for r in range(radius, 0, -3):
        a = int(alpha * (r / radius) ** 0.5)
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=(*color, a)
        )
    img_rgba = img.convert("RGBA")
    img_rgba = Image.alpha_composite(img_rgba, overlay)
    return img_rgba.convert("RGB")


def draw_grade_pill(draw, x, y, label, color, font):
    """Grade badge pill."""
    tw = draw.textlength(label, font=font)
    pw = int(tw + 40)
    ph = 56
    bg = (color[0] // 6, color[1] // 6, color[2] // 6)
    draw.rounded_rectangle([x, y, x + pw, y + ph], radius=12, fill=bg, outline=color, width=3)
    draw.text((x + 20, y + (ph - font.size) // 2 - 2), label, fill=color, font=font)
    return pw


def generate_yt_thumbnail():
    w, h = 1280, 720
    img = Image.new("RGB", (w, h), (8, 12, 18))
    draw_gradient_bg(img, BG_GRADIENT_TOP, BG_GRADIENT_BOT)

    # Glow effects for visual depth
    img = draw_glow_circle(img, 200, 250, 350, ACCENT_GLOW, alpha=18)
    img = draw_glow_circle(img, 1050, 500, 280, GREEN, alpha=12)
    img = draw_glow_circle(img, 640, 100, 200, (100, 60, 200), alpha=8)

    draw = ImageDraw.Draw(img)

    # === TOP RIGHT: "90s DEMO" pill ===
    demo_font = get_font(32, bold=True)
    demo_text = "90s DEMO"
    dtw = draw.textlength(demo_text, font=demo_font)
    demo_x = w - int(dtw) - 60
    demo_y = 28
    draw.rounded_rectangle(
        [demo_x - 18, demo_y - 8, demo_x + int(dtw) + 18, demo_y + 44],
        radius=12, fill=(ACCENT[0] // 3, ACCENT[1] // 3, ACCENT[2] // 3),
        outline=ACCENT, width=3
    )
    draw.text((demo_x, demo_y), demo_text, fill=ACCENT, font=demo_font)

    # === MAIN HEADLINE ===
    headline_font = get_font(96, bold=True)
    draw.text((60, 40), "GRADES STOCKS", fill=TEXT_WHITE, font=headline_font)

    # "NO AI BLACK BOX" - accent colored
    sub_headline_font = get_font(76, bold=True)
    draw.text((60, 150), "NO AI BLACK BOX", fill=ACCENT, font=sub_headline_font)

    # === 3 CORE FEATURES as tagline ===
    tag_font = get_font(28)
    draw.text((64, 248), "Live stock grades \u00b7 Rule-based scoring \u00b7 Auto email reports",
              fill=TEXT_LIGHT, font=tag_font)

    # === GRADE PILLS ===
    pill_font = get_font(24, bold=True)
    pill_y = 310
    x = 64
    x += draw_grade_pill(draw, x, pill_y, "STRONG BUY", GREEN, pill_font) + 18
    x += draw_grade_pill(draw, x, pill_y, "HOLD", YELLOW, pill_font) + 18
    draw_grade_pill(draw, x, pill_y, "AVOID", RED, pill_font)

    # === STOCK CARDS (right side) ===
    card_x = 720
    card_y = 400
    card_w = 500
    card_h = 70
    card_spacing = 84

    stocks = [
        ("NVDA", "US$225.16", "HOLD (3/5)", YELLOW),
        ("SHOP.TO", "CA$214.35", "AVOID (1/5)", RED),
        ("ADANIPORTS.BO", "1,687.60 INR", "STRONG BUY (4/5)", GREEN),
    ]

    ticker_font = get_font(24, bold=True)
    price_font = get_font(17)
    grade_font = get_font(19, bold=True)

    for i, (ticker, price, grade, color) in enumerate(stocks):
        cy = card_y + i * card_spacing
        card_bg = (color[0] // 14, color[1] // 14, color[2] // 14)
        draw.rounded_rectangle(
            [card_x, cy, card_x + card_w, cy + card_h],
            radius=10, fill=card_bg, outline=color, width=2
        )
        # Ticker + price
        draw.text((card_x + 20, cy + 12), ticker, fill=TEXT_WHITE, font=ticker_font)
        draw.text((card_x + 20, cy + 42), price, fill=MUTED, font=price_font)
        # Grade on right
        tw = draw.textlength(grade, font=grade_font)
        draw.text((card_x + card_w - int(tw) - 22, cy + 24), grade, fill=color, font=grade_font)

    # === FEATURE BADGES (bigger text) ===
    feat_y = 420
    feat_font = get_font(20, bold=True)
    features = ["\U0001f4e7 Email Reports", "\U0001f512 Privacy-First", "\U0001f30d USA \u00b7 Canada \u00b7 India"]
    fx = 64
    for feat in features:
        fw = draw.textlength(feat, font=feat_font)
        draw.rounded_rectangle(
            [fx, feat_y, fx + fw + 28, feat_y + 40],
            radius=10, fill=(20, 35, 55), outline=(60, 95, 130), width=2
        )
        draw.text((fx + 14, feat_y + 8), feat, fill=TEXT_LIGHT, font=feat_font)
        fx += int(fw) + 42

    # === METRIC BADGES (bigger) ===
    metrics_y = 540
    metrics_font = get_font(20, bold=True)
    metrics = ["D/E", "PEG", "ROE", "200-SMA", "RSI"]
    mx = 64
    for m in metrics:
        mw = draw.textlength(m, font=metrics_font)
        draw.rounded_rectangle(
            [mx, metrics_y, mx + mw + 24, metrics_y + 36],
            radius=8, fill=(15, 25, 40), outline=(60, 100, 140), width=2
        )
        draw.text((mx + 12, metrics_y + 7), m, fill=TEXT_LIGHT, font=metrics_font)
        mx += int(mw) + 34

    scored_font = get_font(24, bold=True)
    draw.text((mx + 12, metrics_y + 5), "\u2192 scored 0\u20135", fill=TEXT_LIGHT, font=scored_font)

    # === BOTTOM LEFT: "Chrome Extension" ===
    bottom_font = get_font(24, bold=True)
    draw.text((64, 650), "Chrome Extension \u00b7 Open Source \u00b7 Free", fill=MUTED, font=bottom_font)

    # Top accent line
    draw.rectangle([0, 0, w, 5], fill=ACCENT)
    # Bottom accent line
    draw.rectangle([0, h - 4, w, h], fill=ACCENT)

    # Save
    path = os.path.join(OUTPUT_DIR, "yt_thumbnail_1280x720.png")
    img.save(path, "PNG", quality=95)
    print(f"\u2713 YouTube thumbnail: {path} ({os.path.getsize(path):,} bytes)")
    return path


if __name__ == "__main__":
    generate_yt_thumbnail()

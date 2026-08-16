"""
Generate premium-looking Small promo tile (440x280) and Marquee promo tile (1400x560).
Uses actual screenshot compositing + bold typography for a professional marketing look.
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
import os
import math

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(OUTPUT_DIR, "store_ready")

# Brand palette
BG_DARK = (8, 12, 18)
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
    for r in range(radius, 0, -2):
        a = int(alpha * (r / radius) ** 0.5)
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=(*color, a)
        )
    # Composite
    img_rgba = img.convert("RGBA")
    img_rgba = Image.alpha_composite(img_rgba, overlay)
    return img_rgba.convert("RGB")


def draw_accent_bar(draw, x, y, w, h, color, radius=4):
    """Rounded accent bar."""
    draw.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=color)


def draw_grade_pill(draw, x, y, label, color, font):
    """Grade badge pill with glow-like background."""
    tw = draw.textlength(label, font=font)
    pw = int(tw + 24)
    ph = 36
    # Background fill (darker tint of the color)
    bg = (color[0] // 5, color[1] // 5, color[2] // 5)
    draw.rounded_rectangle([x, y, x + pw, y + ph], radius=8, fill=bg, outline=color, width=2)
    draw.text((x + 12, y + 8), label, fill=color, font=font)
    return pw


def draw_feature_block(draw, x, y, icon_color, title, desc, title_font, desc_font):
    """Feature block with colored accent bar + text."""
    # Accent bar
    draw_accent_bar(draw, x, y + 2, 4, 44, icon_color, radius=2)
    draw.text((x + 16, y), title, fill=TEXT_WHITE, font=title_font)
    draw.text((x + 16, y + 28), desc, fill=MUTED, font=desc_font)


# ===========================================================================
# SMALL PROMO TILE — 440x280
# ===========================================================================

def generate_small_tile():
    w, h = 440, 280
    img = Image.new("RGB", (w, h), BG_DARK)
    draw_gradient_bg(img, BG_GRADIENT_TOP, BG_GRADIENT_BOT)

    # Add subtle glow accents
    img = draw_glow_circle(img, 350, 60, 180, ACCENT_GLOW, alpha=18)
    img = draw_glow_circle(img, 80, 240, 120, GREEN, alpha=10)

    draw = ImageDraw.Draw(img)

    # Big brand name
    brand_font = get_font(38, bold=True)
    draw.text((28, 28), "StockAgent", fill=TEXT_WHITE, font=brand_font)

    # Tagline
    tag_font = get_font(15)
    draw.text((30, 75), "Live grades · Rule-based scoring", fill=TEXT_LIGHT, font=tag_font)
    draw.text((30, 96), "Auto email reports · Privacy-first", fill=MUTED, font=tag_font)

    # Grade pills — big and readable
    pill_font = get_font(14, bold=True)
    pill_y = 145
    x = 30
    x += draw_grade_pill(draw, x, pill_y, "STRONG BUY", GREEN, pill_font) + 12
    x += draw_grade_pill(draw, x, pill_y, "HOLD", YELLOW, pill_font) + 12
    draw_grade_pill(draw, x, pill_y, "AVOID", RED, pill_font)

    # Bottom feature strip
    strip_font = get_font(12, bold=True)
    strip_y = 210
    features = ["25 Tickers", "US · CA · IN", "No AI Scoring", "Email Digests"]
    feat_x = 30
    for feat in features:
        fw = draw.textlength(feat, font=strip_font)
        draw.text((feat_x, strip_y), feat, fill=MUTED, font=strip_font)
        # Small dot separator
        feat_x += int(fw) + 8
        if feat != features[-1]:
            draw.ellipse([feat_x, strip_y + 5, feat_x + 4, strip_y + 9], fill=ACCENT)
            feat_x += 12

    # Bottom accent line
    draw.rectangle([0, h - 4, w, h], fill=ACCENT)

    # Subtle top-right decoration
    for i in range(3):
        bar_h = [35, 55, 80][i]
        bar_x = 370 + i * 22
        bar_y = 220 - bar_h
        opacity = [80, 140, 220][i]
        bar_color = (ACCENT[0] * opacity // 255, ACCENT[1] * opacity // 255, ACCENT[2] * opacity // 255)
        draw.rounded_rectangle([bar_x, bar_y, bar_x + 14, 220], radius=4, fill=bar_color)

    path = os.path.join(STORE_DIR, "small_promo_tile_440x280.png")
    img.save(path, "PNG")
    print(f"✓ Small tile: {path} ({os.path.getsize(path):,} bytes)")


# ===========================================================================
# MARQUEE PROMO TILE — 1400x560
# ===========================================================================

def generate_marquee_tile():
    w, h = 1400, 560
    img = Image.new("RGB", (w, h), BG_DARK)
    draw_gradient_bg(img, BG_GRADIENT_TOP, BG_GRADIENT_BOT)

    # Glow effects for depth
    img = draw_glow_circle(img, 300, 150, 300, ACCENT_GLOW, alpha=15)
    img = draw_glow_circle(img, 1100, 400, 250, GREEN, alpha=8)
    img = draw_glow_circle(img, 700, 500, 200, (100, 60, 200), alpha=6)

    draw = ImageDraw.Draw(img)

    # === LEFT SIDE: Text content ===

    # Brand name — large and bold
    brand_font = get_font(62, bold=True)
    draw.text((70, 60), "StockAgent", fill=TEXT_WHITE, font=brand_font)

    # Tagline — clear value prop
    tag_font = get_font(24)
    draw.text((74, 135), "Live stock grades, rule-based scoring", fill=TEXT_LIGHT, font=tag_font)
    draw.text((74, 168), "& auto email reports", fill=TEXT_LIGHT, font=tag_font)

    # Feature blocks with accent bars
    feat_title = get_font(18, bold=True)
    feat_desc = get_font(14)

    draw_feature_block(draw, 74, 240, GREEN,
                       "Transparent Grades", "5 metrics · no black box", feat_title, feat_desc)
    draw_feature_block(draw, 74, 310, ACCENT,
                       "Scheduled Reports", "Pick days, times & timezone", feat_title, feat_desc)
    draw_feature_block(draw, 74, 380, YELLOW,
                       "Privacy-First", "Holdings stay on your device", feat_title, feat_desc)
    draw_feature_block(draw, 74, 450, (180, 140, 255),
                       "Multi-Region", "US · Canada · India — 25 tickers", feat_title, feat_desc)

    # === RIGHT SIDE: Grade showcase ===

    # Large grade pills — the hero visual
    pill_font_lg = get_font(22, bold=True)
    grades = [
        ("STRONG BUY (4/5)", GREEN),
        ("HOLD (3/5)", YELLOW),
        ("AVOID (1/5)", RED),
    ]

    card_x = 780
    card_y_start = 100
    card_spacing = 90

    for i, (label, color) in enumerate(grades):
        cy = card_y_start + i * card_spacing

        # Card background
        card_w = 520
        card_h = 70
        card_bg = (color[0] // 12, color[1] // 12, color[2] // 12)
        draw.rounded_rectangle(
            [card_x, cy, card_x + card_w, cy + card_h],
            radius=12, fill=card_bg, outline=color, width=2
        )

        # Ticker name
        ticker_font = get_font(20, bold=True)
        tickers = ["VFV.TO", "NVDA", "TSLA"][i]
        draw.text((card_x + 20, cy + 12), tickers, fill=TEXT_WHITE, font=ticker_font)

        # Price
        price_font = get_font(14)
        prices = ["191.64 CAD", "225.16 USD", "342.27 USD"][i]
        draw.text((card_x + 20, cy + 40), prices, fill=MUTED, font=price_font)

        # Grade badge on right side of card
        tw = draw.textlength(label, font=pill_font_lg)
        badge_x = card_x + card_w - int(tw) - 40
        draw.text((badge_x, cy + 22), label, fill=color, font=pill_font_lg)

    # Metrics example below cards
    metrics_y = card_y_start + 3 * card_spacing + 20
    metrics_font = get_font(13)
    metric_labels = ["D/E", "PEG", "ROE", "200-SMA", "RSI"]
    mx = card_x
    for ml in metric_labels:
        mw = draw.textlength(ml, font=metrics_font)
        # Metric pill
        draw.rounded_rectangle(
            [mx, metrics_y, mx + mw + 16, metrics_y + 26],
            radius=6, fill=(20, 30, 45), outline=(50, 70, 95)
        )
        draw.text((mx + 8, metrics_y + 5), ml, fill=TEXT_LIGHT, font=metrics_font)
        mx += int(mw) + 28

    # "Scored 0–5" label
    score_font = get_font(15)
    draw.text((card_x, metrics_y + 40), "Each scored 0–5 · deterministic · fully explainable",
              fill=MUTED, font=score_font)

    # Bottom accent line
    draw.rectangle([0, h - 5, w, h], fill=ACCENT)

    # Bottom-left text
    bottom_font = get_font(13)
    draw.text((70, h - 35), "Open source · Free · No account required for grades",
              fill=MUTED, font=bottom_font)

    path = os.path.join(STORE_DIR, "marquee_promo_tile_1400x560.png")
    img.save(path, "PNG")
    print(f"✓ Marquee tile: {path} ({os.path.getsize(path):,} bytes)")


if __name__ == "__main__":
    os.makedirs(STORE_DIR, exist_ok=True)
    generate_small_tile()
    generate_marquee_tile()
    print("\n✓ Done — upload both from Chrome_Store_Screenshots/store_ready/")

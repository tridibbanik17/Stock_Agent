"""
Generate YouTube thumbnail (1280x720) for Stock Agent QuantumHacks demo video.
Hackathon-focused: emphasizes the project name, problem solved, and key differentiators.
"""

from PIL import Image, ImageDraw, ImageFont
import os

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

# Brand palette
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
PURPLE = (140, 100, 240)


def get_font(size, bold=False):
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
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for y in range(h):
        t = y / h
        r = int(top_color[0] * (1 - t) + bot_color[0] * t)
        g = int(top_color[1] * (1 - t) + bot_color[1] * t)
        b = int(top_color[2] * (1 - t) + bot_color[2] * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))


def draw_glow_circle(img, cx, cy, radius, color, alpha=40):
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for r in range(radius, 0, -3):
        a = int(alpha * (r / radius) ** 0.5)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, a))
    img_rgba = img.convert("RGBA")
    img_rgba = Image.alpha_composite(img_rgba, overlay)
    return img_rgba.convert("RGB")


def generate_hackathon_thumbnail():
    w, h = 1280, 720
    img = Image.new("RGB", (w, h), (8, 12, 18))
    draw_gradient_bg(img, BG_GRADIENT_TOP, BG_GRADIENT_BOT)

    # Glow effects
    img = draw_glow_circle(img, 180, 200, 320, ACCENT_GLOW, alpha=20)
    img = draw_glow_circle(img, 1000, 550, 300, GREEN, alpha=12)
    img = draw_glow_circle(img, 900, 120, 200, PURPLE, alpha=10)

    draw = ImageDraw.Draw(img)

    # === TOP LEFT: QuantumHacks badge ===
    hack_font = get_font(22, bold=True)
    hack_text = "QUANTUMHACKS 2026"
    htw = draw.textlength(hack_text, font=hack_font)
    draw.rounded_rectangle(
        [50, 28, 50 + htw + 30, 28 + 38],
        radius=8, fill=(PURPLE[0] // 4, PURPLE[1] // 4, PURPLE[2] // 4),
        outline=PURPLE, width=2
    )
    draw.text((65, 35), hack_text, fill=PURPLE, font=hack_font)

    # === TOP RIGHT: "DEMO" badge ===
    demo_font = get_font(26, bold=True)
    demo_text = "4:50 DEMO"
    dtw = draw.textlength(demo_text, font=demo_font)
    demo_x = w - int(dtw) - 70
    draw.rounded_rectangle(
        [demo_x - 16, 26, demo_x + int(dtw) + 16, 26 + 42],
        radius=10, fill=(ACCENT[0] // 4, ACCENT[1] // 4, ACCENT[2] // 4),
        outline=ACCENT, width=2
    )
    draw.text((demo_x, 32), demo_text, fill=ACCENT, font=demo_font)

    # === MAIN HEADLINE: "Stock Agent" ===
    brand_font = get_font(108, bold=True)
    draw.text((55, 85), "Stock Agent", fill=TEXT_WHITE, font=brand_font)

    # === SUBHEADLINE ===
    sub_font = get_font(40, bold=True)
    draw.text((62, 220), "Privacy-First Stock Grading", fill=ACCENT, font=sub_font)

    # === 3 CORE FEATURES ===
    feat_font = get_font(28, bold=True)
    feat_desc_font = get_font(24)
    
    features = [
        ("\u2713 Live stock grades", GREEN),
        ("\u2713 Rule-based scoring (no AI)", GREEN),
        ("\u2713 Auto email reports", GREEN),
    ]
    fy = 290
    for feat_text, color in features:
        draw.text((68, fy), feat_text, fill=color, font=feat_font)
        fy += 44

    # === STOCK CARDS (right side) ===
    card_x = 700
    card_y = 290
    card_w = 520
    card_h = 64
    card_spacing = 78

    stocks = [
        ("NVDA", "US$225.16", "HOLD (3/5)", YELLOW),
        ("SHOP.TO", "CA$214.35", "AVOID (1/5)", RED),
        ("ADANIPORTS.BO", "\u20b91,687.60", "STRONG BUY (4/5)", GREEN),
    ]

    ticker_font = get_font(22, bold=True)
    price_font = get_font(16)
    grade_font = get_font(18, bold=True)

    for i, (ticker, price, grade, color) in enumerate(stocks):
        cy = card_y + i * card_spacing
        card_bg = (color[0] // 14, color[1] // 14, color[2] // 14)
        draw.rounded_rectangle(
            [card_x, cy, card_x + card_w, cy + card_h],
            radius=10, fill=card_bg, outline=color, width=2
        )
        draw.text((card_x + 18, cy + 10), ticker, fill=TEXT_WHITE, font=ticker_font)
        draw.text((card_x + 18, cy + 38), price, fill=MUTED, font=price_font)
        tw = draw.textlength(grade, font=grade_font)
        draw.text((card_x + card_w - int(tw) - 20, cy + 22), grade, fill=color, font=grade_font)

    # === METRIC PILLS (bottom-left area) ===
    metrics_y = 470
    metrics_font = get_font(22, bold=True)
    metrics = ["D/E", "PEG", "ROE", "200-SMA", "RSI"]
    mx = 68
    for m in metrics:
        mw = draw.textlength(m, font=metrics_font)
        draw.rounded_rectangle(
            [mx, metrics_y, mx + mw + 26, metrics_y + 38],
            radius=8, fill=(15, 25, 40), outline=(60, 100, 140), width=2
        )
        draw.text((mx + 13, metrics_y + 7), m, fill=TEXT_LIGHT, font=metrics_font)
        mx += int(mw) + 36

    # "Scored 0-5" next to metrics
    scored_font = get_font(24, bold=True)
    draw.text((mx + 10, metrics_y + 6), "\u2192 Scored 0\u20135", fill=TEXT_LIGHT, font=scored_font)

    # === REGIONS ===
    region_y = 540
    region_font = get_font(24, bold=True)
    draw.text((68, region_y), "\U0001f30d  USA \u00b7 Canada \u00b7 India", fill=TEXT_LIGHT, font=region_font)

    # === BOTTOM FEATURE BADGES ===
    badge_y = 600
    badge_font = get_font(20, bold=True)
    badges = [
        ("\U0001f512 Holdings Never Leave Device", TEXT_LIGHT),
        ("\U0001f4e7 Scheduled Reports", TEXT_LIGHT),
        ("\U0001f916 Optional AI Explain (BYOK)", TEXT_LIGHT),
    ]
    bx = 68
    for badge_text, color in badges:
        bw = draw.textlength(badge_text, font=badge_font)
        draw.rounded_rectangle(
            [bx, badge_y, bx + bw + 24, badge_y + 36],
            radius=8, fill=(18, 30, 48), outline=(50, 80, 115), width=1
        )
        draw.text((bx + 12, badge_y + 7), badge_text, fill=color, font=badge_font)
        bx += int(bw) + 38

    # === TECH STACK (bottom right) ===
    stack_font = get_font(16, bold=True)
    stack_y = 660
    stack_text = "Chrome MV3 \u00b7 FastAPI \u00b7 Supabase \u00b7 yfinance \u00b7 Resend \u00b7 AWS Lambda"
    stw = draw.textlength(stack_text, font=stack_font)
    draw.text((w - int(stw) - 50, stack_y), stack_text, fill=MUTED, font=stack_font)

    # Accent lines
    draw.rectangle([0, 0, w, 5], fill=ACCENT)
    draw.rectangle([0, h - 4, w, h], fill=GREEN)

    # Save
    path = os.path.join(OUTPUT_DIR, "yt_thumbnail_hackathon_1280x720.png")
    img.save(path, "PNG", quality=95)
    print(f"\u2713 Hackathon YT thumbnail: {path} ({os.path.getsize(path):,} bytes)")
    return path


if __name__ == "__main__":
    generate_hackathon_thumbnail()
